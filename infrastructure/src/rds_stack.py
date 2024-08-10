from constructs import Construct
from aws_cdk import Stack, CfnOutput
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_iam as iam


class RDSStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Create a VPC with isolated subnets
        self.vpc = ec2.Vpc(
            self,
            "PostgresVectorDBVPC",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC
                ),
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                ec2.SubnetConfiguration(
                    name="isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
            ],
        )

        # Create a security group for the EC2 instance and the rds instance
        sg = ec2.SecurityGroup(
            self,
            "SecurityGroup",
            vpc=self.vpc,
            description="Allow SSH and PostgreSQL",
            security_group_name="CDK-SG-EC2-RDS",
        )
        self.rds_sg = ec2.SecurityGroup(
            self,
            "RDSSecurityGroup",
            vpc=self.vpc,
            description="Security group for RDS instance",
        )
        # Create a Lambda security group so that the lambda can access the DB later.
        self.lambda_sg = ec2.SecurityGroup(
            self,
            "LambdaSecurityGroup",
            vpc=self.vpc,
            description="Security group for Lambda function",
        )
        self.rds_sg.add_ingress_rule(
            peer=self.lambda_sg,
            connection=ec2.Port.tcp(5432),
            description="Allow Lambda to connect to RDS",
        )
        self.lambda_sg.add_egress_rule(
            peer=self.rds_sg,
            connection=ec2.Port.tcp(5432),
            description="Allow Lambda to connect to RDS",
        )

        # Add rules to the security group
        # sg.add_egress_rule(
        #     ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(5432), "Allow PostgreSQL traffic within VPC"
        # )
        sg.add_egress_rule(
            peer=self.rds_sg,
            connection=ec2.Port.tcp(5432),
            description="Allow EC2 Bastion Host to send traffic to the RDS instance.",
        )

        # Allow HTTPS traffic for SSM connections to the Bastion host.
        sg.add_egress_rule(
            ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            ec2.Port.tcp(443),
            "Allow HTTPS to SSM VPC Endpoint",
        )

        # Create an IAM role for the EC2 instance
        role = iam.Role(
            self,
            "InstanceSSMRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                )
            ],
        )

        # Allow inbound PostgreSQL traffic from bastion host's security group
        self.rds_sg.add_ingress_rule(
            peer=sg,
            connection=ec2.Port.tcp(5432),
            description="Allow PostgreSQL access from EC2 bastion host",
        )

        # Define the EC2 instance
        instance = ec2.Instance(
            self,
            "Instance",
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            vpc=self.vpc,
            role=role,
            security_group=sg,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # Create a database secret
        db_secret = secretsmanager.Secret(
            self,
            "DBSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"postgres"}',
                generate_string_key="password",
                exclude_characters='@/" ',
            ),
        )

        # RDS Instance with IAM Authentication
        self.db_instance = rds.DatabaseInstance(
            self,
            "PostgresVectorDB",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_15_6
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MICRO
            ),
            vpc=self.vpc,
            security_groups=[self.rds_sg],
            vpc_subnets={"subnet_type": ec2.SubnetType.PRIVATE_ISOLATED},
            iam_authentication=True,
            multi_az=False,
            allocated_storage=20,
            max_allocated_storage=100,
            credentials=rds.Credentials.from_secret(db_secret),
        )

        # IAM role for accessing the database
        db_access_role = iam.Role(
            self,
            "DBAccessRole",
            assumed_by=iam.ServicePrincipal("rds.amazonaws.com"),
            inline_policies={
                "DBAccessPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["rds-db:connect"],
                            resources=[
                                f"arn:aws:rds-db:{self.region}:{self.account}:dbuser:*/{self.db_instance.instance_identifier}"
                            ],
                        )
                    ]
                )
            },
        )

        # Output the instance ID and other relevant info
        CfnOutput(self, "InstanceID", value=instance.instance_id)
        CfnOutput(
            self,
            "PostgresVectorDBID",
            value=self.db_instance.db_instance_endpoint_address,
        )
        # Output the security group ID and endpoint address
        CfnOutput(self, "RdsSecurityGroupId", value=self.rds_sg.security_group_id)
