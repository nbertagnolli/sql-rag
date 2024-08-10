import pandas as pd
import os
import argparse
import json
import boto3
from utils import (
    get_secret,
    create_sqlalchemy_connection,
    create_connection,
    add_query_to_db,
)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Database connection arguments")
    parser.add_argument(
        "--secret-name",
        required=True,
        help="The name of the secret in AWS Secrets Manager",
    )
    parser.add_argument(
        "--ssl-path",
        required=True,
        help="The full path to the ssl pem file.",
    )
    parser.add_argument(
        "--seed-data-path",
        required=True,
        help="The path to a folder of csvs you want to create tables for",
    )
    parser.add_argument(
        "--initialize-queries",
        required=False,
        type=bool,
        default=True,
        help="The path to a folder of csvs you want to create tables for",
    )
    args = parser.parse_args()

    # Get the secret name
    creds = get_secret(args.secret_name)
    credentials = {
        "password": creds["password"],
        "ssl_path": args.ssl_path,
        "host": creds["host"],
        "port": 1053,
    }
    conn = create_sqlalchemy_connection(**credentials)

    # Load in the hubspot data and create some sqlite tables.
    file_names = os.listdir(args.seed_data_path)
    for file_name in file_names:
        print(file_name)
        if file_name != ".DS_Store":
            table_name = file_name.split(".")[0].replace("-", "_")
            df = pd.read_csv(
                os.path.join(args.seed_data_path, file_name), encoding="utf-8"
            )
            conn = create_sqlalchemy_connection(**credentials)
            df.to_sql(table_name, conn, if_exists="replace", index=False)

    # Enable the Vector extension for PGSQL
    conn = create_connection(**credentials)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    conn.close()

    # Create a database to hold user queries
    conn = create_connection(**credentials)
    cur = conn.cursor()
    table_create_command = """
    CREATE TABLE user_queries (
                id bigserial PRIMARY KEY, 
                user_query text,
                sql_query text,
                conversation_history text
                );
                """

    cur.execute(table_create_command)
    cur.close()
    conn.commit()

    # Create the table for storing sql queries and their embedding
    conn = create_connection(**credentials)
    cur = conn.cursor()
    table_create_command = """
    CREATE TABLE queries (
                id bigserial PRIMARY KEY, 
                name text,
                query text,
                args text ARRAY,
                arg_types text ARRAY,
                embedding vector(384)  -- bge-small-en is 384 dim
                );
                """

    cur.execute(table_create_command)
    cur.close()
    conn.commit()

    # Setup temporary queries.
    if args.initialize_queries:
        QUERIES = [
            (
                "total_revenue_by_country",
                """
    -- This query calculates total revenue by country/region.
    SELECT 
        "Country/Region", 
        SUM("Annual Revenue") AS total_revenue
    FROM 
        all_companies
    GROUP BY 
        "Country/Region"
    ORDER BY 
        total_revenue {order};
    """,
                ["order"],
                ["string"],
            ),
            (
                "average_annual_revenue_by_closing",
                """
    -- The average annual revenue of institutions with a high chance of closing?
    SELECT 
        AVG("Annual Revenue") AS average_annual_revenue
    FROM 
        all_companies
    -- Filter the rows to include only those with a high likelihood of closing.
    WHERE 
        "Likelihood to close" >= {likelihood_threshold};
    """,
                ["likelihood_threshold"],
                ["number"],
            ),
            (
                "top_10_companies_by_revenue",
                """
    -- This query lists the top 10 companies based on their annual revenue.
    SELECT 
        "Company name", 
        "Annual Revenue"
    FROM 
        all_companies
    ORDER BY 
        "Annual Revenue" {order}
    LIMIT {limit};
    """,
                ["order", "limit"],
                ["string", "number"],
            ),
            (
                "average_likelihood_to_close_by_industry",
                """
    -- This query calculates the average likelihood to close deals by industry.
    SELECT 
        Industry, 
        AVG("Likelihood to close") AS average_likelihood_to_close
    FROM 
        all_companies
    GROUP BY 
        Industry
    ORDER BY 
        average_likelihood_to_close {order};
    """,
                ["order"],
                ["string"],
            ),
        ]

    for name, query, args, arg_types in QUERIES:
        runtime = boto3.client("sagemaker-runtime")
        input_data = {"text": query}
        response = runtime.invoke_endpoint(
            EndpointName="query-embedding",
            ContentType="application/json",
            Body=json.dumps(input_data),
        )
        embedding = json.loads(response["Body"].read().decode())
        conn = create_connection(**credentials)
        add_query_to_db(conn, name, query, args, arg_types, embedding)
        conn.close()
