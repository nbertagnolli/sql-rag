from aws_cdk import (
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from .rds_stack import RDSStack
from .api_stack import APILambdaStack
from .model_stack import SageMakerInferenceStack
from constructs import Construct


class MainStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.inference_stack = SageMakerInferenceStack(self, "InferenceStack")
        self.rds_stack = RDSStack(self, "RDSStack")

        self.lambda_api = APILambdaStack(
            self,
            "APILambdaStack",
            self.inference_stack.sagemaker_policy,
            self.rds_stack.vpc,
            self.rds_stack.db_instance.db_instance_endpoint_address,
            self.rds_stack.lambda_sg,
        )
