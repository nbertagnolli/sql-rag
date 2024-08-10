import argparse
import os
import tarfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

import boto3


def get_sagemaker_role_arn(stack_name) -> str:
    cf_client = boto3.client("cloudformation", region_name="us-west-2")

    response = cf_client.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0]["Outputs"]
    return [o for o in outputs if o["OutputKey"] == "SageMakerRoleARN"][0][
        "OutputValue"
    ]


def tar_folder(tar_dir, output_file) -> None:
    """Compress a folder into the tar.gz format.

    :param tar_dir: The directory to compress.
    :param output_file: The name of the compressed file.
    """
    with tarfile.open(output_file, "w:gz") as tar:
        tar.add(tar_dir, arcname=os.path.sep)


def check_if_endpoint_exists(client, endpoint_name: str) -> bool:
    """Check if a sagemaker endpoint with name `endpoint_name` exists."""
    endpoints = client.list_endpoints()["Endpoints"]
    return any([endpoint_name == x["EndpointName"] for x in endpoints])


def create_endpoint(
    client,
    role_arn: str,
    endpoint_name: str,
    production_variant: Dict[str, Any],
    container_list: List[Dict[str, Any]],
    vpc_config: Dict[str, Any] = None,
) -> Dict[str, Any]:

    uid = uuid.uuid4().hex
    config_name = endpoint_name + "-" + uid

    client.create_model(
        ModelName=config_name,
        ExecutionRoleArn=role_arn,
        Containers=container_list,
    )

    production_variant["ModelName"] = config_name
    config = {
        "EndpointConfigName": config_name,
        "ProductionVariants": [production_variant],
    }
    if vpc_config is not None:
        config["VpcConfig"] = vpc_config
    client.create_endpoint_config(**config)

    if check_if_endpoint_exists(client, endpoint_name):
        # If the endpoint already exists update it.
        response = client.update_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=config_name,
        )
    else:
        # Create a new endpoint if it doesn't already exist.
        response = client.create_endpoint(
            EndpointName=endpoint_name, EndpointConfigName=config_name
        )

    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Deploy the dreambooth model to sagemaker."
    )
    parser.add_argument(
        "--model-folder",
        type=str,
        default="embedding",
        help="the folder to deploy",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="The maximum number of concurrent serverless instances.",
    )
    args = parser.parse_args()
    model_name = args.model_folder.replace("_", "-")

    # Get all of the necessary infrastructure bits from cdk cloudformation outputs
    cf_client = boto3.client("cloudformation", region_name="us-west-2")
    stack_name = "MainStackInferenceStackBC3C657F"

    response = cf_client.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0]["Outputs"]

    # The bucket is the part of the arn after the :::
    s3_bucket = [o for o in outputs if o["OutputKey"] == "FilesBucketARN"][0][
        "OutputValue"
    ].split(":::")[-1]
    sagemaker_role_arn = get_sagemaker_role_arn(stack_name)

    tar_folder(
        Path(__file__).parent / args.model_folder,
        "model.tar.gz",
    )

    s3_client = boto3.client("s3")
    s3_path = f"models/{model_name}/model.tar.gz"
    print(f"Uploading files to {s3_bucket}/{s3_path}")
    s3_client.upload_file("model.tar.gz", s3_bucket, s3_path)

    # Deploy the endpoint
    sagemaker_client = boto3.client("sagemaker", region_name="us-west-2")

    # TODO:: production_variant and container should be configured with the models themselves not here in code.
    #  later we can setup a scheme to include them with the models.
    production_variant = {
        "VariantName": "AllTraffic",
        "InitialVariantWeight": 1,
        "ServerlessConfig": {
            "MemorySizeInMB": 6144,
            "MaxConcurrency": args.max_concurrency,
        },
    }
    # https://github.com/aws/deep-learning-containers/blob/master/available_images.md
    # image = "763104351884.dkr.ecr.us-west-2.amazonaws.com/huggingface-pytorch-inference:1.13.1-transformers4.26.0-cpu-py39-ubuntu20.04"
    image = "763104351884.dkr.ecr.us-west-2.amazonaws.com/huggingface-pytorch-inference:2.1.0-transformers4.37.0-cpu-py310-ubuntu22.04"
    container_list = [
        {
            "ContainerHostname": "huggingface-pytorch-inference",
            "Image": image,
            "Mode": "SingleModel",
            "ModelDataUrl": f"s3://{s3_bucket}/{s3_path}",
            "Environment": {
                "SAGEMAKER_CONTAINER_LOG_LEVEL": "20",
                "SAGEMAKER_REGION": "us-west-2",
                "BUCKET_NAME": s3_bucket,
            },
        },
    ]

    endpoint_name = f"query-{model_name}"

    # response = cf_client.describe_stacks(StackName=stack_name)
    # print(response)
    # lambda_sg = [o for o in outputs if o["OutputKey"] == "LambdaSGID"][0]["OutputValue"]
    # subnets = eval(
    #     [o for o in outputs if o["OutputKey"] == "Subnets"][0]["OutputValue"]
    # )
    # print(lambda_sg)
    # print(subnets)
    # vpc_config = {
    #     "SecurityGroupIds": ["sg-0fa57c356809dadba"],
    #     "Subnets": ["subnet-01a89568ffe1d7ae2", "subnet-0ccc59a3c1f5a7deb"],
    # }

    create_endpoint(
        sagemaker_client,
        sagemaker_role_arn,
        endpoint_name,
        production_variant,
        container_list,
    )
