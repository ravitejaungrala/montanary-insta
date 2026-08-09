import csv
import io
import logging
from datetime import datetime
from core.config import S3_BUCKET_NAME
from core.s3_utils import get_s3_client

logger = logging.getLogger("pipelyt")

def append_lead_to_csv(data: dict):
    file_key = "leads/contact_leads.csv"
    
    # Header definition
    header = ["timestamp", "firstName", "lastName", "email", "phone", "company", "message", "source"]
    
    # Prepare row
    row = {
        "timestamp": datetime.now().isoformat(),
        "firstName": data.get("firstName"),
        "lastName": data.get("lastName"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "company": data.get("company"),
        "message": data.get("message"),
        "source": data.get("source")
    }
    
    try:
        s3_client = get_s3_client()
        if not s3_client or not S3_BUCKET_NAME:
            logger.error("S3 is not configured; cannot persist contact lead.")
            return False

        # 1. Download existing file or create new buffer
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=file_key)
            existing_content = response["Body"].read().decode("utf-8")
            buffer = io.StringIO(existing_content)
            # Find the end of the file
            buffer.seek(0, io.SEEK_END)
        except s3_client.exceptions.NoSuchKey:
            logger.info(f"CSV file {file_key} not found. Creating new one.")
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=header)
            writer.writeheader()
        except Exception as e:
            if "NoSuchBucket" in str(e):
                logger.error(f"S3 Bucket {S3_BUCKET_NAME} does not exist.")
                return False
            raise e
        
        # 2. Append new row
        writer = csv.DictWriter(buffer, fieldnames=header)
        writer.writerow(row)
        
        # 3. Upload back to S3
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=file_key,
            Body=buffer.getvalue(),
            ContentType="text/csv"
        )
        logger.info(f"Successfully appended lead for {data.get('email')} to S3.")
        return True
    except Exception as e:
        logger.error(f"Failed to save lead to S3: {str(e)}")
        return False
