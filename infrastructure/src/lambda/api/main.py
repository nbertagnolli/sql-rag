from fastapi import FastAPI
from mangum import Mangum
import uvicorn
from typing import Any, Dict, List, Optional
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector
import psycopg2
from psycopg2 import OperationalError
import boto3
import json
import os
from openai import OpenAI
import traceback
from pydantic import BaseModel


class ChatSQLOutput(BaseModel):
    sql_query: str
    notes: str


OPENAI_CLIENT = OpenAI(
    # This is the default and can be omitted
    api_key=os.environ.get("OPENAI_API_KEY"),
)


CREDENTIALS = {
    "password": os.getenv("DB_PASSWORD", "Tvzh*f]uvxX?`y(L$u`Vyra&b6P9VQQ4"),
    "host": os.getenv(
        "DB_HOST",
        "mainstackrdsstackb4b88b4d-postgresvectordb82399e33-adgdgkmbo427.cbas6w2cunpd.us-west-2.rds.amazonaws.com",
    ),
    "port": os.getenv("DB_PORT", "1053"),
}

SQL_PROMPT = """
I ran this query on my database:

```
{table_query}
```

 and got the following result:

 ```
 {table_results}
 ```

As a senior analyst working in postgres, given the above schemas and data, write a detailed and correct postgres query to answer the analytical question:

{user_query}

Your output should be structured as follows:

1. `sql_query`: Only the raw SQL query that can be executed directly in psql, without any comments or explanations.
2. `notes`: A brief description of what the SQL query does, but this is optional. The `sql_query` should contain only the SQL query itself.

Make sure the `sql_query` is a valid SQL query and contains no comments or additional text.
"""


def add_query_to_db(conn, query: str, args: List[str], embedding: List[float]):
    # Add queries to the database
    # Batch insert embeddings and metadata from dataframe into PostgreSQL database
    register_vector(conn)
    cur = conn.cursor()
    # Prepare the list of tuples to insert
    data_list = [(query, "{" + ",".join([f'"{x}"' for x in args]) + "}", embedding)]
    # Use execute_values to perform batch insertion
    execute_values(
        cur, "INSERT INTO queries (query, args, embedding) VALUES %s", data_list
    )
    # Commit after we insert all embeddings
    conn.commit()


def create_connection(host: str, port: str, password: str):
    """Create a database connection using psycopg2 with SSL using environment variables."""
    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            dbname="postgres",
            user="postgres",  # use the dbuser with iam auth!
            password=password,
        )
        print("Secure connection to PostgreSQL DB successful")
    except OperationalError as e:
        print(f"The error '{e}' occurred")
    return connection


def get_embedding(query: str):
    runtime = boto3.client("sagemaker-runtime")
    input_data = {"text": query}
    response = runtime.invoke_endpoint(
        EndpointName="query-embedding",
        ContentType="application/json",
        Body=json.dumps(input_data),
    )
    return json.loads(response["Body"].read().decode())


def get_similar(query_embedding, conn, n: int = 3):
    embedding_str = f'[{", ".join(map(str, query_embedding))}]'
    # Register pgvector extension
    register_vector(conn)
    cur = conn.cursor()
    # Get the most similar words using the KNN <=> operator
    cur.execute(
        f"SELECT name, query, args, arg_types, (embedding <=> %s) as similarity FROM queries ORDER BY similarity LIMIT {n}",
        (embedding_str,),
    )
    return cur.fetchall()


def call_db(query: str, **kwargs):
    """This function is a universal DB call.

    It works by allowing partial application of SQL queries with defined arguments.
    """
    query = query.format(**kwargs)
    conn = create_connection(**CREDENTIALS)
    try:
        cur = conn.cursor()
        cur.execute(query)
        result = cur.fetchall()
        return result
    except:
        pass
    finally:
        conn.close()


def format_query_spec_to_openai_tool(
    name: str, query: str, args: List[str], arg_types: List[str]
) -> Dict[str, Any]:
    properties = {
        arg_name: {"title": arg_name, "type": arg_type}
        for arg_name, arg_type in zip(args, arg_types)
    }
    func_spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name}({', '.join(args)}) - This function will call the following query {query} with arguments",
            "parameters": {
                "title": f"{name}_schema",
                "type": "object",
                "properties": properties,
                "required": args,
            },
        },
    }
    return func_spec


app = FastAPI()
handler = Mangum(app, lifespan="off")


class QueryRequest(BaseModel):
    query: str


class AddQuery(BaseModel):
    query: str
    args: List[str]


@app.get("/")
def read_root():
    return {"status": "healthy"}


@app.get("/test")
def test_db_connection():
    # Get the credentials and connect to the database
    conn = create_connection(**CREDENTIALS)
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM queries LIMIT 5")
    res = cur.fetchall()
    conn.close()
    return res


@app.post("/add")
def add_query(query: AddQuery):
    """Adds a query to the database."""
    # Embedd sql
    embedding = get_embedding(query.query)

    # Get the credentials and connect to the database
    conn = create_connection(**CREDENTIALS)

    # Update Vector Table with new SQL query and embedding
    add_query_to_db(conn, query.query, query.args, embedding)
    conn.close()

    # Return True if added
    return {"status": "success"}


@app.get("/find")
def find_query(query: str, n: int = 5):
    """Adds a query to the database."""
    # Embedd request
    embedding = get_embedding(query)

    # Auth with database
    conn = create_connection(**CREDENTIALS)

    # Query Table for similar queries
    result = get_similar(embedding, conn, n=n)
    conn.close()
    return result


@app.post("/query")
def query_with_language(query: QueryRequest):
    # Determine if we should use function calling
    embedding = get_embedding(query.query)

    # Auth with database
    conn = create_connection(**CREDENTIALS)

    # Query Table for similar queries
    similar_sql_queries = get_similar(embedding, conn, n=5)

    # Do Function Calling
    # FIXME:: THE OUTPUT OF GET SIMILAR SHOULD REALLY BE A SENSIBLE DICT LOL.....
    print(similar_sql_queries)
    if similar_sql_queries[0][-1] < 0.1:
        # Create the tool specs
        tools = [
            format_query_spec_to_openai_tool(q[0], q[1], q[2], q[3])
            for q in similar_sql_queries
        ]
        # Run the function call
        messages = [
            {"role": "system", "content": ""},
            {
                "role": "user",
                "content": f"{query} use your best judgement.",
            },
        ]
        chat_out = OPENAI_CLIENT.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools
        )
        finish_reason = chat_out.choices[0].finish_reason
        fn_name = chat_out.choices[0].message.tool_calls[0].function.name
        print(chat_out)
        if finish_reason == "tool_calls":
            for res in similar_sql_queries:
                if res[0] == fn_name:
                    # FIXME:: Types aren't perfect. GPT doesn't know when to use float or number.
                    #   This could probably be solved with better variable names
                    fn_query = (
                        res[1]
                        .strip()
                        .format(
                            **json.loads(
                                chat_out.choices[0]
                                .message.tool_calls[0]
                                .function.arguments
                            )
                        )
                    )
                    conn = create_connection(**CREDENTIALS)
                    cur = conn.cursor()
                    cur.execute(fn_query)
                    out = cur.fetchall()
                    conn.close()
                    print(chat_out.choices[0].message.tool_calls[0].function.arguments)
                    return out

    # If we don't find a sufficiently close query in our database OR ChatGPT
    # decides not to do a function call we default to chatGPT running the show.
    # First query our database to get context on ALL tables.
    conn = create_connection(**CREDENTIALS)
    cur = conn.cursor()
    table_query = """
    SELECT 
        table_schema, 
        table_name, 
        column_name, 
        data_type, 
        is_nullable, 
        column_default
    FROM 
        information_schema.columns
    WHERE 
        table_schema NOT IN ('information_schema', 'pg_catalog')
    ORDER BY 
        table_schema, 
        table_name, 
        ordinal_position;
    """
    cur.execute(table_query)
    table_results = cur.fetchall()
    conn.close()

    # Query ChatGPT for the sql query to run.
    messages = [
        {"role": "system", "content": ""},
        {
            "role": "user",
            "content": SQL_PROMPT.format(
                table_query=table_query,
                table_results=table_results,
                user_query=query.query,
            ),
        },
    ]
    client = OpenAI(
        # This is the default and can be omitted
        api_key=os.environ.get("OPENAI_API_KEY"),
    )
    result = client.beta.chat.completions.parse(
        model="gpt-4o-2024-08-06",
        messages=messages,
        response_format=ChatSQLOutput,
    )

    # Run the query with one fix retry
    # FIXME:: THIS SHOULD BE A function with configurable retries and convergence testing.
    try:
        conn = create_connection(**CREDENTIALS)
        cur = conn.cursor()
        cur.execute(json.loads(result.choices[0].message.content)["sql_query"])
        out = cur.fetchall()
        conn.close()
    except Exception:
        # If we fail try and fix the query.
        e = traceback.format_exc()
        print(e)
        # Extract the previous response and add it to the conversation history
        messages.append(
            {"role": "assistant", "content": result.choices[0].message.content}
        )
        messages.append(
            {
                "role": "user",
                "content": f"This query didn't run. We got this error {e} please fix the query so that it will run.",
            }
        )
        result = client.beta.chat.completions.parse(
            model="gpt-4o-2024-08-06",
            messages=messages,
            response_format=ChatSQLOutput,
        )
        conn = create_connection(**CREDENTIALS)
        cur = conn.cursor()
        cur.execute(json.loads(result.choices[0].message.content)["sql_query"])
        out = cur.fetchall()
        conn.close()
    finally:
        # We want to run this saving no matter what happens so that we can debug failures
        # Store request and response only when new things come through.
        # FIXME:: This should be a background task for better performance. This
        # doesn't work on lambdas since they have to exit on return so I'm not doing that
        # here. But you'll want this as a background task if you deploy this API for realz.
        conn = create_connection(**CREDENTIALS)
        cur = conn.cursor()

        # Example values to insert
        sql_query = json.loads(result.choices[0].message.content)["sql_query"]
        conversation_history = json.dumps(messages)

        # Construct the SQL INSERT statement
        insert_command = """
        INSERT INTO user_queries (user_query, sql_query, conversation_history) 
        VALUES (%s, %s, %s);
        """

        # Execute the command
        cur.execute(insert_command, (query.query, sql_query, conversation_history))

        # Commit the transaction and close the connection
        conn.commit()
        cur.close()
        conn.close()

    # Return Response
    return out


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
