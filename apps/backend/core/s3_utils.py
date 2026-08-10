import os
import boto3
import uuid
from core.config import S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION

def get_s3_client():
    """
    Initializes a boto3 S3 client.
    Handles both Local (keys provided) and Lambda (IAM Role/Env) authentication paths.
    """
    if not S3_BUCKET_NAME:
        print("DEBUG: S3_BUCKET_NAME not configured")
        return None
        
    client_kwargs = {'region_name': AWS_REGION or 'us-east-1'}
    
    # If explicit credentials are provided (local or hydrated in Lambda), use them
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        client_kwargs['aws_access_key_id'] = AWS_ACCESS_KEY_ID
        client_kwargs['aws_secret_access_key'] = AWS_SECRET_ACCESS_KEY

    try:
        return boto3.client('s3', **client_kwargs)
    except Exception as e:
        print(f"DEBUG: Failed to initialize S3 client: {str(e)}")
        return None

def get_s3_url(filename):
    """Generates a standard public S3 URL for a given filename."""
    if not S3_BUCKET_NAME or not AWS_REGION:
        return None
    return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{filename}"

def upload_fileobj_to_s3(file_obj, filename, bucket_name=S3_BUCKET_NAME):
    """Uploads a file object to S3 and returns the public URL."""
    s3 = get_s3_client()
    if not s3:
        return None
    
    try:
        s3.upload_fileobj(file_obj, bucket_name, filename)
        return get_s3_url(filename)
    except Exception as e:
        print(f"DEBUG: S3 Upload Failed: {str(e)}")
        return None
