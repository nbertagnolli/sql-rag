# Use the official AWS Lambda Python base image
FROM public.ecr.aws/lambda/python:3.10

# Install any dependencies
RUN pip install --no-cache-dir fastapi mangum uvicorn psycopg2-binary openai requests boto3 pgvector

# Copy the function code
COPY main.py ${LAMBDA_TASK_ROOT}

# Command to run the Lambda function
CMD ["main.handler"]