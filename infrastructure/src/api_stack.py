from constructs import Construct
from aws_cdk import (
    Stack,
    aws_lambda as _lambda,
    CfnOutput,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_logs as logs,
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
import os
import aws_cdk as cdk


class APILambdaStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        sagemaker_policy,
        vpc,
        host_name,
        lambda_sg,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create a Log Group for the Lambda function
        self.log_group = logs.LogGroup(
            self,
            "APILambdaLogGroup",
            log_group_name=f"/aws/lambda/APIFunction",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK,
        )

        # Define the Lambda function with a very simple Hello World response inline
        self.api_fn = _lambda.DockerImageFunction(
            self,
            "APIFunction",
            code=_lambda.DockerImageCode.from_image_asset(
                os.path.join(os.path.dirname(__file__), "lambda/api"),
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
            timeout=cdk.Duration.seconds(600),
            memory_size=1024,
            log_group=self.log_group,
            environment={
                "DB_HOST": host_name,
                "DB_NAME": "postgres",
                "DB_USER": "postgres",
                "DB_PASSWORD": "...",  # TODO: GET THIS AUTOMATICALLY
                "DB_PORT": "5432",
                "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            },
            vpc=vpc,
            security_groups=[lambda_sg],
        )

        # Create a function URL that we can call to invoke the lambda function.
        self.api_fn_url = self.api_fn.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM
        )

        # Create an IAM user with permissions to invoke the Lambda function
        iam_user = iam.User(self, "LambdaInvokerUser")
        policy = iam.Policy(
            self,
            "LambdaInvokePolicy",
            statements=[
                iam.PolicyStatement(
                    actions=["lambda:InvokeFunctionUrl"],
                    resources=[self.api_fn.function_arn],
                )
            ],
        )
        iam_user.attach_inline_policy(policy)

        # Create access keys for the IAM user
        access_key = iam.AccessKey(self, "APILambdaInvokerUserAccessKey", user=iam_user)

        # Store the access keys in Secrets Manager
        secret = secretsmanager.Secret(
            self,
            id="APILambdaInvokerUserCredentials",
            secret_object_value={
                "AWS_ACCESS_KEY_ID": cdk.SecretValue.unsafe_plain_text(
                    access_key.access_key_id
                ),
                "AWS_SECRET_ACCESS_KEY": access_key.secret_access_key,
            },
        )

        # Add the ability to call SageMaker to this endpoint role
        self.api_fn.add_to_role_policy(sagemaker_policy)

        sagemaker_vpc_endpoint = ec2.InterfaceVpcEndpoint(
            self,
            "SageMakerVPCEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_RUNTIME,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[lambda_sg],
        )

        # Allow Lambda security group to communicate with the SageMaker VPC endpoint
        lambda_sg.add_egress_rule(
            peer=lambda_sg,
            connection=ec2.Port.tcp(443),
            description="Allow Lambda to communicate with SageMaker VPC Endpoint",
        )

        # Write out the lambda url to the stack output for easy access.
        CfnOutput(self, "LambdaUrl", value=self.api_fn_url.url)
        CfnOutput(self, "SecretArn", value=secret.secret_arn)
        CfnOutput(self, "LambdaSGID", value=lambda_sg.security_group_id)
        CfnOutput(
            self,
            "Subnets",
            value=f"{[subnet.subnet_id for subnet in vpc.private_subnets]}",
        )
