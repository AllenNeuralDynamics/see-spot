"""
S3 Handler Module for the view2p-E application.
Manages connections and operations with AWS S3.
"""

import os
import logging
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from typing import Optional, Union
# Configure logging
logger = logging.getLogger(__name__)

class S3Handler:
    """Handler for S3 operations."""
    
    def __init__(self, bucket_name=None):
        """
        Initialize S3 client using environment credentials.
        
        Args:
            bucket_name (str, optional): Default S3 bucket name.
        """
        self.s3_client = None
        self.s3_resource = None
        self.bucket_name = bucket_name
        self.init_s3_client()
    
    def init_s3_client(self):
        """Initialize the S3 client using credentials from environment variables."""
        try:
            # Create S3 client - boto3 will automatically use AWS_ACCESS_KEY_ID, 
            # AWS_SECRET_ACCESS_KEY, and AWS_SESSION_TOKEN from environment
            self.s3_client = boto3.client('s3')
            self.s3_resource = boto3.resource('s3')
            logger.info("S3 client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            raise
    
    def test_connection(self, bucket_name=None):
        """
        Test connection to S3 by listing objects in a bucket.
        
        Args:
            bucket_name (str, optional): S3 bucket name to test. Uses default if not provided.
            
        Returns:
            dict: Test results with success status and message
        """
        bucket = bucket_name or self.bucket_name
        
        if not bucket:
            return {
                "success": False,
                "message": "No bucket name provided for test"
            }
        
        try:
            # Try to list objects (limited to 5 for test)
            response = self.s3_client.list_objects_v2(
                Bucket=bucket,
                MaxKeys=5
            )
            
            # Check if we can access the bucket
            if 'Contents' in response:
                object_count = len(response['Contents'])
                objects = [obj['Key'] for obj in response['Contents']]
                
                return {
                    "success": True,
                    "message": f"Successfully connected to bucket '{bucket}'",
                    "object_count": object_count,
                    "sample_objects": objects
                }
            else:
                return {
                    "success": True,
                    "message": f"Successfully connected to bucket '{bucket}' but it appears to be empty"
                }
                
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            if error_code == 'NoSuchBucket':
                return {
                    "success": False,
                    "message": f"Bucket '{bucket}' does not exist"
                }
            elif error_code == 'AccessDenied':
                return {
                    "success": False,
                    "message": f"Access denied to bucket '{bucket}'. Check your credentials and permissions."
                }
            else:
                return {
                    "success": False,
                    "message": f"Error accessing bucket '{bucket}': {error_message}"
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"Unexpected error: {str(e)}"
            }

    def list_objects(self, bucket_name=None, prefix="", max_keys=1000):
        """
        List objects in a bucket with optional prefix filtering.
        
        Args:
            bucket_name (str, optional): S3 bucket name. Uses default if not provided.
            prefix (str, optional): Filter objects by prefix
            max_keys (int, optional): Maximum number of keys to return
            
        Returns:
            list: List of object keys
        """
        bucket = bucket_name or self.bucket_name
        
        if not bucket:
            logger.error("No bucket name provided")
            return []
        
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            objects = []
            
            # Paginate through results
            for page in paginator.paginate(
                Bucket=bucket,
                Prefix=prefix,
                PaginationConfig={"MaxItems": max_keys}
            ):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects.append(obj['Key'])
            
            return objects
            
        except Exception as e:
            logger.error(f"Error listing objects in bucket '{bucket}': {e}")
            return []
    
    def get_object(self, key, bucket_name=None):
        """
        Get an object from S3.
        
        Args:
            key (str): Object key
            bucket_name (str, optional): S3 bucket name. Uses default if not provided.
            
        Returns:
            bytes: Object data or None if error
        """
        bucket = bucket_name or self.bucket_name
        
        if not bucket:
            logger.error("No bucket name provided")
            return None
        
        try:
            response = self.s3_client.get_object(
                Bucket=bucket,
                Key=key
            )
            return response['Body'].read()
        except Exception as e:
            logger.error(f"Error getting object '{key}' from bucket '{bucket}': {e}")
            return None

    def get_object_metadata(self, key, bucket_name=None):
        """
        Get metadata for an object in S3, including size.

        Args:
            key (str): Object key
            bucket_name (str, optional): S3 bucket name. Uses default if not provided.

        Returns:
            dict: Object metadata or None if error
        """
        bucket = bucket_name or self.bucket_name

        if not bucket:
            logger.error("No bucket name provided for metadata retrieval")
            return None

        try:
            # Use head_object to get metadata without downloading the body
            response = self.s3_client.head_object(
                Bucket=bucket,
                Key=key
            )
            # Return relevant metadata
            return {
                'ContentLength': response.get('ContentLength'),
                'LastModified': response.get('LastModified'),
                'ContentType': response.get('ContentType'),
                'ETag': response.get('ETag')
                # Add other metadata fields from response if needed
            }
        except ClientError as e:
            # Handle common errors like Not Found
            if e.response['Error']['Code'] == '404':
                 logger.warning(f"Object '{key}' not found in bucket '{bucket}'.")
            else:
                logger.error(f"Error getting metadata for object '{key}' from bucket '{bucket}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting metadata for '{key}': {e}")
            return None


    def download_file(
        self,
        key: str,
        bucket_name: Optional[str] = None,
        local_path: Optional[Union[str, Path]] = None,
        use_cache: bool = True,
        cache_dir: Union[str, Path] = '/s3-cache'
    ) -> Optional[Path]:
        """
        Downloads a file from S3, optionally using a local cache.

        Args:
            key (str): S3 object key.
            bucket_name (str, optional): S3 bucket name. Uses handler's default if None.
            local_path (str | Path, optional): Specific local path to download to.
                If provided, caching logic is skipped.
            use_cache (bool, optional): If True (default), check cache before downloading.
                Ignored if local_path is provided.
            cache_dir (str | Path, optional): Root directory for local caching.
                Defaults to '/s3-cache'. Ignored if local_path is provided.

        Returns:
            Path: Path object to the local file (cached or downloaded), or None on error.
        """
        bucket = bucket_name or self.bucket_name
        if not bucket:
            logger.error("No bucket name provided for download.")
            return None

        if self.s3_client is None:
             logger.error("S3 client is not initialized.")
             return None

        effective_local_path: Path
        is_cache_path = False

        if local_path:
            # User specified an exact download location
            effective_local_path = Path(local_path).resolve() # Resolve to absolute path
            logger.info(f"Direct download requested to: {effective_local_path}")
            use_cache = False # Explicit path overrides cache usage check
        else:
            # Construct path within the cache directory
            is_cache_path = True
            base_cache_dir = Path(cache_dir).resolve() # Resolve cache dir path
            # Combine cache base, bucket, and key to form path
            # Ensure key is treated as relative within the bucket folder
            safe_key_part = key.lstrip('/')
            effective_local_path = base_cache_dir / bucket / safe_key_part
            logger.debug(f"Cache path constructed: {effective_local_path}")

            # Check cache if requested and applicable
            if use_cache and effective_local_path.is_file(): # Check if it's actually a file
                logger.info(f"Cache hit! Using local file: {effective_local_path}")
                # Optional: Could add check here to compare S3 etag/last_modified
                # with cached file metadata if cache invalidation is needed.
                return effective_local_path
            elif use_cache:
                 logger.info(f"Cache miss or not a file: {effective_local_path}")
            # If not using cache or file not found, proceed to download

        # --- Download required ---
        logger.info(f"Attempting to download s3://{bucket}/{key} to {effective_local_path}")

        try:
            # Ensure parent directory exists
            effective_local_path.parent.mkdir(parents=True, exist_ok=True)

            # Use download_file for efficient transfer to disk
            self.s3_client.download_file(
                Bucket=bucket,
                Key=key,
                Filename=str(effective_local_path) # download_file expects a string path
            )
            logger.info(f"Successfully downloaded to: {effective_local_path}")
            return effective_local_path

        except ClientError as e:
            # Check for specific errors like Not Found
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == '404' or 'NoSuchKey' in str(e): # Check common variations
                logger.error(f"Error: Object not found on S3: s3://{bucket}/{key}")
            elif error_code == 'NoSuchBucket':
                 logger.error(f"Error: Bucket not found: {bucket}")
            else:
                logger.error(f"S3 ClientError during download for key '{key}': {e}")
            # Consider removing partially downloaded file if download_file guarantees creation
            # Check if file exists and maybe size is 0 before unlinking
            # try:
            #     if effective_local_path.is_file() and effective_local_path.stat().st_size == 0:
            #          logger.warning(f"Removing potentially incomplete file due to error: {effective_local_path}")
            #          effective_local_path.unlink()
            # except OSError as unlink_err:
            #      logger.error(f"Error removing incomplete file {effective_local_path}: {unlink_err}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during download of '{key}': {e}", exc_info=True)
            return None

# Create a global instance for easy access
# s3_handler = S3Handler('aind-open-data')
s3_handler = S3Handler('codeocean-s3resultsbucket-1182nktl2bh9f')

# Test function that can be called to verify connection
def test_s3_connection():
    """Test S3 connection and print results."""
    results = s3_handler.test_connection()
    
    if results["success"]:
        print(f"✅ {results['message']}")
        
        if "sample_objects" in results:
            print(f"\nFound {results['object_count']} objects. Sample objects:")
            for obj in results["sample_objects"]:
                print(f"  - {obj}")
    else:
        print(f"❌ {results['message']}")
    
    return results

# For direct testing
if __name__ == "__main__":
    test_s3_connection() 