from sqlalchemy import create_engine
import psycopg2
from psycopg2 import OperationalError

import boto3
from botocore.exceptions import ClientError
from typing import Optional
import json
import base64
import numpy as np
import psycopg2
from typing import List
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector


def get_secret(
    secret_name: str,
    region_name: str = "us-west-2",
    session: Optional[boto3.Session] = None,
) -> Optional[str]:
    """
    Get the secret with secret name from AWS secret manager.

    Code snippet from https://aws.amazon.com/developers/getting-started/python/.

    :param secret_name: The name of the secret in secrets manager.
    :param region_name: The AWS region where the secret is stored.
    :return secret: The json object of secret values.
    """
    # Create a Secrets Manager client
    if session is None:
        session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)

    # In this sample we handle the specific exceptions for the 'GetSecretValue' API.
    # See https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html  # noqa: E501
    # We rethrow the exception by default.

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "DecryptionFailureException":
            # Secrets Manager can't decrypt the protected secret text using the provided
            # KMS key. Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "InternalServiceErrorException":
            # An error occurred on the server side.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "InvalidParameterException":
            # You provided an invalid value for a parameter.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "InvalidRequestException":
            # You provided a parameter value that is not valid for the current state of
            # the resource. Deal with the exception here, and/or rethrow at your
            # discretion.
            raise e
        elif e.response["Error"]["Code"] == "ResourceNotFoundException":
            # We can't find the resource that you asked for.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
    else:
        # Decrypts secret using the associated KMS key.
        # Depending on whether the secret is a string or binary, one of these fields
        # will be populated.
        if "SecretString" in get_secret_value_response:
            secret = get_secret_value_response["SecretString"]
            return json.loads(secret)
        else:
            decoded_binary_secret = base64.b64decode(
                get_secret_value_response["SecretBinary"]
            )
            return json.loads(decoded_binary_secret)

    return None


def create_connection(host: str, port: str, password: str, ssl_path: str):
    """Create a database connection using psycopg2 with SSL using environment variables."""
    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            dbname="postgres",
            user="postgres",  # use the dbuser with iam auth!
            password=password,
            sslmode="verify-full",  # Ensure SSL usage and server certificate verification
            sslrootcert=ssl_path,
        )
        print("Secure connection to PostgreSQL DB successful")
    except OperationalError as e:
        print(f"The error '{e}' occurred")
    return connection


def create_sqlalchemy_connection(host: str, port: str, password: str, ssl_path: str):
    """Create a database connection using SQLAlchemy with SSL using environment variables."""
    try:
        DATABASE_URL = (
            f"postgresql+psycopg2://postgres:{password}@"
            f"{host}:{port}/postgres"
            f"?sslmode=verify-full&sslrootcert={ssl_path}"
        )

        # Create an SQLAlchemy engine
        engine = create_engine(DATABASE_URL)
        return engine

    except Exception as e:
        print(f"The error '{e}' occurred")
        return None


def add_query_to_db(
    conn,
    name: str,
    query: str,
    args: List[str],
    arg_types: List[str],
    embedding: np.array,
):
    # Add queries to the database
    # Batch insert embeddings and metadata from dataframe into PostgreSQL database
    register_vector(conn)
    cur = conn.cursor()
    # Prepare the list of tuples to insert
    data_list = [
        (
            name,
            query,
            "{" + ",".join([f'"{x}"' for x in args]) + "}",
            "{" + ",".join([f'"{x}"' for x in arg_types]) + "}",
            embedding,
        )
    ]
    # Use execute_values to perform batch insertion
    execute_values(
        cur,
        "INSERT INTO queries (name, query, args, arg_types, embedding) VALUES %s",
        data_list,
    )
    # Commit after we insert all embeddings
    conn.commit()
