from constructs import Construct
from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    CfnOutput,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_logs as logs,
    aws_ecr_assets as ecr_assets,
    aws_s3 as s3,
)
import os
import aws_cdk as cdk


class SageMakerInferenceStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # the webservice is a CRUD application for files stored in an S3 bucket
        self.files_bucket = s3.Bucket(
            self,
            id="files-bucket",
            auto_delete_objects=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Create a sagemaker role that can read from S3 and deploy models.
        self.sagemaker_role = iam.Role(
            self,
            id="sagemaker-role",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
        )
        self.sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess")
        )
        self.sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess")
        )
        self.sagemaker_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite")
        )

        # Create a policy for invoking SageMaker endpoints. This can be used by other stacks
        # To grant permissions to their resources.
        self.sagemaker_policy = iam.PolicyStatement(
            actions=[
                "sagemaker:GetModelPackageGroupPolicy",
                "sagemaker:ListEndpointConfigs",
                "sagemaker:ListEndpoints",
                "sagemaker:ListModels",
                "iam:PassRole",
                "sagemaker:InvokeEndpoint",
            ],
            effect=iam.Effect.ALLOW,
            resources=["*"],
        )

        cdk.CfnOutput(
            self,
            id="FilesBucketARN",
            description="S3 Bucket for storing sagemaker files",
            value=self.files_bucket.bucket_arn,
        )

        cdk.CfnOutput(
            self,
            id="SageMakerRoleARN",
            description="Role for deploying sagemaker models",
            value=self.sagemaker_role.role_arn,
        )
