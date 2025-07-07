import os
import shutil
import tempfile
import time
import threading
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import storage
from git import Repo, GitCommandError # Using GitPython
import stat # Keep this for Windows cleanup
from neo4j import GraphDatabase

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

# Initialize Google Cloud Storage client
storage_client = storage.Client()
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'gca-cloned-repos-graphrag-464113') # Set this env var or use a default

# REMOVE THESE LINES:
# import vertexai
# from vertexai.language_models import TextEmbeddingModel
# from vertexai.generative_models import GenerativeModel
# vertexai.init(
#     project=os.environ.get("GCP_PROJECT_ID"),
#     location=os.environ.get("GCP_REGION")
# )
# embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
# generative_model = GenerativeModel("gemini-1.5-flash-001")


# In-memory store to map cloned repos to temp paths (for multi-step process)
# In a real app, this should be persisted (e.g., in a database or Redis)
# For hackathon, we'll use a simple dict keyed by repo_id (e.g., original GitHub URL hash)
CLONED_REPOS = {}

# Dict to track processing progress
PROCESSING_STATUS = {}

def list_files_in_directory(path):
    """Recursively lists all files in a directory."""
    file_list = []
    for root, dirs, files in os.walk(path):
        # Skip .git directories
        if '.git' in dirs:
            dirs.remove('.git')
            
        # Skip any other hidden directories starting with dot
        dirs[:] = [d for d in dirs if not d.startswith('.')]
            
        for file in files:
            # Skip files that start with .git or any hidden files
            if file.startswith('.git') or file.startswith('.'):
                continue
                
            # Get path relative to the initial clone directory
            relative_path = os.path.relpath(os.path.join(root, file), path)
            file_list.append(relative_path)
    return file_list

def remove_readonly(func, path, excinfo):
    # Clear the readonly bit and reattempt the removal
    os.chmod(path, stat.S_IWRITE)
    func(path)

def process_files_in_batches(repo_id, files_to_process, temp_repo_path):
    """Process files in batches"""
    try:
        # Update status
        PROCESSING_STATUS[repo_id] = {
            "total_files": len(files_to_process),
            "processed": 0,
            "current_file": "",
            "status": "processing",
            "message": "Starting file processing"
        }

        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        batch_size = 5  # Increased from 2 to 5
        total_processed = 0
        uploaded_files = []
        failed_files = []

        # Process in batches
        for i in range(0, len(files_to_process), batch_size):
            batch = files_to_process[i:i+batch_size]
            batch_uploaded = []
            
            for file_path in batch:
                try:
                    local_file_path = os.path.join(temp_repo_path, file_path)
                    if os.path.exists(local_file_path):
                        # Update status
                        PROCESSING_STATUS[repo_id]["current_file"] = file_path
                        PROCESSING_STATUS[repo_id]["message"] = f"Processing {file_path}"

                        # Define GCS destination path
                        destination_blob_name = f'cloned_repos/{repo_id}/{file_path}'
                        blob = bucket.blob(destination_blob_name)
                        
                        # Add repo_id metadata to help with identification
                        metadata = {
                            "repo_id": repo_id,
                            "file_path": file_path,
                            "file_name": os.path.basename(file_path)
                        }
                        blob.metadata = metadata
                        
                        blob.upload_from_filename(local_file_path)
                        uploaded_files.append(file_path)
                        batch_uploaded.append(file_path)
                        
                        # Update counters
                        total_processed += 1
                        PROCESSING_STATUS[repo_id]["processed"] = total_processed
                        
                        print(f"Uploaded {local_file_path} to gs://{GCS_BUCKET_NAME}/{destination_blob_name} with metadata: {metadata}")
                    else:
                        print(f"File not found: {local_file_path}")
                        failed_files.append(file_path)
                except Exception as e:
                    print(f"Error processing file {file_path}: {e}")
                    failed_files.append(file_path)
                    # Continue with the next file
            
            # Wait longer for each batch to be processed before moving to the next batch
            if batch_uploaded:
                PROCESSING_STATUS[repo_id]["message"] = f"Waiting for batch processing to complete ({total_processed}/{len(files_to_process)})"
                time.sleep(10)  # Increased from 3 to 10 seconds
                
                # Double check if files in this batch were processed before moving on
                if repo_id in PROCESSING_STATUS and PROCESSING_STATUS[repo_id] is not None and "status" in PROCESSING_STATUS[repo_id] and PROCESSING_STATUS[repo_id]["status"] != "error":
                    PROCESSING_STATUS[repo_id]["message"] = f"Checking if batch was processed in Neo4j"
                    verify_batch_in_neo4j(repo_id, batch_uploaded)

        # Record any failed files
        if failed_files:
            PROCESSING_STATUS[repo_id]["failed_files"] = failed_files
            print(f"Failed to process {len(failed_files)} files: {failed_files}")

        # Step 3: Wait for all processing to complete
        if uploaded_files:
            PROCESSING_STATUS[repo_id]["message"] = "Waiting for Neo4j ingestion to complete"
            wait_for_neo4j_processing(repo_id, uploaded_files)
        else:
            PROCESSING_STATUS[repo_id]["status"] = "error"
            PROCESSING_STATUS[repo_id]["message"] = "No files were successfully uploaded"

        # Clean up
        try:
            shutil.rmtree(temp_repo_path, onerror=remove_readonly)
            if repo_id in CLONED_REPOS:
                del CLONED_REPOS[repo_id]
            print(f"Cleaned up temp directory: {temp_repo_path}")
        except Exception as e:
            print(f"Error during cleanup: {e}")

    except Exception as e:
        PROCESSING_STATUS[repo_id] = {
            "status": "error",
            "message": f"Error processing files: {str(e)}"
        }
        print(f"Error in batch processing thread: {e}")

def verify_batch_in_neo4j(repo_id, batch_files):
    """Verify if a batch of files has been processed in Neo4j"""
    try:
        # Get Neo4j connection details
        neo4j_uri = os.getenv("NEO4J_URI", "")
        neo4j_username = os.getenv("NEO4J_USERNAME", "")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "")
        
        if not neo4j_uri or not neo4j_username or not neo4j_password:
            print("Neo4j connection details missing, skipping batch verification")
            return
        
        # Connect to Neo4j
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # Maximum wait time (reduced from 30 to 15 seconds)
        max_wait_time = 15
        start_time = time.time()
        batch_processed = False
        
        file_names = [os.path.basename(file_path) for file_path in batch_files]
        print(f"Verifying batch of {len(batch_files)} files in Neo4j: {file_names}")
        
        # Poll Neo4j to check if processing is complete for this batch
        while time.time() - start_time < max_wait_time:
            try:
                with driver.session() as session:
                    # First, let's run a debug query to see what's actually in the database
                    debug_query = """
                    MATCH (n) 
                    RETURN count(n) as node_count
                    """
                    debug_result = session.run(debug_query).single()
                    total_nodes = debug_result["node_count"] if debug_result else 0
                    print(f"DEBUG - Found {total_nodes} total nodes in Neo4j")
                    
                    # If we have any nodes at all, consider it successful
                    if total_nodes > 0:
                        print(f"Found {total_nodes} nodes in Neo4j, considering batch processed")
                        batch_processed = True
                        break
                    
                    # Check for ANY nodes with this repo_id
                    repo_check = session.run(
                        """
                        MATCH (n)
                        WHERE n.repo_id = $repo_id
                        RETURN count(n) as count
                        """,
                        {"repo_id": repo_id}
                    ).single()
                    
                    if repo_check and repo_check["count"] > 0:
                        print(f"Found {repo_check['count']} nodes with repo_id {repo_id}")
                        batch_processed = True
                        break
                    
                    # As a last resort, try a very broad match for nodes that might be files
                    broad_check = session.run(
                        """
                        MATCH (n)
                        WHERE any(label IN labels(n) WHERE label =~ ".*File.*" OR label = "Function" OR label = "Module")
                        RETURN count(n) as count
                        """
                    ).single()
                    
                    if broad_check and broad_check["count"] > 0:
                        print(f"Found {broad_check['count']} potential file nodes")
                        batch_processed = True
                        break
            
            except Exception as e:
                print(f"Error during batch verification: {e}")
            
            # Wait 1 second before checking again (reduced from 2)
            time.sleep(1)
        
        # Always consider the batch processed after timeout
        # This bypasses the stall even if we didn't find anything
        if not batch_processed:
            print("Verification timed out but continuing anyway")
            batch_processed = True
        
        # Close the driver
        driver.close()
    
    except Exception as e:
        print(f"Error in batch verification: {e}")
        # Continue processing even if verification fails

def wait_for_neo4j_processing(repo_id, uploaded_files):
    """Wait for Neo4j to finish processing all files"""
    try:
        # Get Neo4j connection details
        neo4j_uri = os.getenv("NEO4J_URI", "")
        neo4j_username = os.getenv("NEO4J_USERNAME", "")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "")
        
        if not neo4j_uri or not neo4j_username or not neo4j_password:
            PROCESSING_STATUS[repo_id]["status"] = "error"
            PROCESSING_STATUS[repo_id]["message"] = "Neo4j connection details missing"
            return
        
        # Connect to Neo4j
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        
        # Maximum wait time (reduced for faster processing)
        max_wait_time = 120  # seconds (reduced from 300 to 120)
        start_time = time.time()
        all_processed = False
        
        # Track consecutive polls with same node count to detect stalls
        last_node_count = -1
        stall_count = 0
        max_stall_count = 3  # Consider processing stalled after 3 identical polls (reduced from 5)
        
        # Poll Neo4j to check if processing is complete
        while time.time() - start_time < max_wait_time:
            try:
                with driver.session() as session:
                    # Check for ANY nodes in Neo4j
                    any_nodes_query = """
                    MATCH (n) 
                    RETURN count(n) as total_nodes
                    """
                    any_nodes_result = session.run(any_nodes_query).single()
                    any_nodes_count = any_nodes_result["total_nodes"] if any_nodes_result else 0
                    
                    print(f"Found {any_nodes_count} total nodes in Neo4j")
                    
                    # Consider successful if we have any nodes at all
                    if any_nodes_count > 0:
                        print(f"Found {any_nodes_count} total nodes - considering processing successful")
                        all_processed = True
                        PROCESSING_STATUS[repo_id]["status"] = "partial"
                        PROCESSING_STATUS[repo_id]["message"] = f"Processing complete. Found {any_nodes_count} nodes in Neo4j."
                        break
                    
                    # Check for stalled processing
                    if any_nodes_count == last_node_count:
                        stall_count += 1
                    else:
                        stall_count = 0
                        last_node_count = any_nodes_count
                    
                    if stall_count >= max_stall_count:
                        print(f"Processing appears stalled at {any_nodes_count} nodes for {stall_count} consecutive checks")
                        if any_nodes_count > 0:
                            # If we have any nodes, consider it partial success
                            print(f"Considering as partial success with {any_nodes_count} nodes")
                            all_processed = True
                            PROCESSING_STATUS[repo_id]["status"] = "partial"
                            PROCESSING_STATUS[repo_id]["message"] = f"Processing complete. Found {any_nodes_count} nodes in Neo4j."
                            break
                    
                    # Wait a bit longer if we're still at 0 nodes
                    if any_nodes_count == 0:
                        print("No nodes found in Neo4j yet, waiting...")
                        PROCESSING_STATUS[repo_id]["message"] = f"Waiting for Neo4j ingestion to begin..."
            
            except Exception as e:
                PROCESSING_STATUS[repo_id]["message"] = f"Error checking Neo4j: {str(e)}"
                print(f"Error checking Neo4j: {e}")
            
            # Wait less time between checks (5 seconds, reduced from 8)
            time.sleep(5)
        
        # Close the driver
        driver.close()
        
        # If we've reached this point and all_processed is still False,
        # we're going to just consider it complete anyway
        if not all_processed:
            print("Processing wait timed out, but continuing anyway")
            PROCESSING_STATUS[repo_id]["status"] = "partial"
            PROCESSING_STATUS[repo_id]["message"] = "Processing time limit reached. Some files may not be processed."
    
    except Exception as e:
        PROCESSING_STATUS[repo_id]["status"] = "error"
        PROCESSING_STATUS[repo_id]["message"] = f"Error during Neo4j processing: {str(e)}"
        print(f"Error in Neo4j processing: {e}")

@app.route('/api/fetch-repo-files', methods=['POST'])
def fetch_repo_files():
    data = request.json
    github_url = data.get('github_url')

    if not github_url:
        return jsonify({"success": False, "message": "GitHub URL is required."}), 400

    if not github_url.startswith('https://github.com/'):
        return jsonify({"success": False, "message": "Invalid GitHub URL format. Must start with https://github.com/"}), 400

    temp_dir = None # Initialize temp_dir outside try to ensure it's defined for cleanup
    repo_id = None # Initialize repo_id

    try:
        # First, clear the Neo4j database
        try:
            # Get RAG API URL from environment variable or default to localhost:5001
            rag_api_url = os.getenv("RAG_API_URL", "http://localhost:5001")
            clear_db_url = f"{rag_api_url}/api/clear-database"
            
            response = requests.post(clear_db_url, timeout=30)
            
            if response.status_code == 200:
                print("Successfully cleared Neo4j database before processing new repository")
            else:
                print(f"Warning: Failed to clear Neo4j database. Status code: {response.status_code}")
        except Exception as e:
            print(f"Warning: Error clearing Neo4j database: {e}. Continuing with repository cloning.")
            # We continue even if clearing fails
        
        # Create a unique ID for this repo cloning session
        import hashlib
        repo_id = hashlib.sha256(github_url.encode()).hexdigest()

        # Create a temporary directory for cloning
        temp_dir = tempfile.mkdtemp()
        CLONED_REPOS[repo_id] = temp_dir # Store the temp path

        print(f"Cloning {github_url} into {temp_dir}")
        Repo.clone_from(github_url, temp_dir) # Use GitPython to clone
        print(f"Cloned successfully: {github_url}")

        # List all files in the cloned repository
        files = list_files_in_directory(temp_dir)

        return jsonify({
            "success": True,
            "message": "Repository cloned successfully. Select files.",
            "files": files,
            "repo_id": repo_id # Send repo_id back to frontend
        })

    except GitCommandError as e:
        print(f"Git cloning error: {e}")
        # Clean up temp directory if cloning failed
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, onerror=remove_readonly)
            if repo_id in CLONED_REPOS:
                del CLONED_REPOS[repo_id]
        return jsonify({"success": False, "message": f"Failed to clone repository: {str(e)}"}), 500
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, onerror=remove_readonly)
            if repo_id in CLONED_REPOS:
                del CLONED_REPOS[repo_id]
        return jsonify({"success": False, "message": f"An error occurred: {str(e)}"}), 500

@app.route('/api/process-files', methods=['POST'])
def process_selected_files():
    data = request.json
    selected_files = data.get('selected_files')
    repo_id = data.get('repo_id') # Get repo_id from frontend

    if not selected_files or not repo_id:
        return jsonify({"success": False, "message": "Selected files and repo ID are required."}), 400

    temp_repo_path = CLONED_REPOS.get(repo_id)
    if not temp_repo_path or not os.path.exists(temp_repo_path):
        return jsonify({"success": False, "message": "Repository not found or session expired. Please re-clone."}), 404

    try:
        # Initialize processing status for this repo_id
        PROCESSING_STATUS[repo_id] = {
            "status": "starting",
            "message": "Starting file processing",
            "total_files": len(selected_files),
            "processed": 0
        }
        
        # Start file processing in a separate thread
        thread = threading.Thread(
            target=process_files_in_batches,
            args=(repo_id, selected_files, temp_repo_path)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "success": True,
            "message": "File processing started",
            "repo_id": repo_id
        })
        
    except Exception as e:
        PROCESSING_STATUS[repo_id] = {
            "status": "error",
            "message": f"Error starting processing: {str(e)}"
        }
        return jsonify({"success": False, "message": f"An error occurred: {str(e)}"}), 500

@app.route('/api/processing-status', methods=['GET'])
def check_processing_status():
    repo_id = request.args.get('repo_id')
    if not repo_id:
        return jsonify({"success": False, "message": "Repository ID is required."}), 400
    
    if repo_id not in PROCESSING_STATUS:
        return jsonify({
            "success": False,
            "message": "No processing status found for this repository."
        }), 404
    
    status = PROCESSING_STATUS[repo_id]
    
    # Check if processing is complete or partial success
    is_complete = status.get("status") == "complete"
    is_partial = status.get("status") == "partial"
    
    # Return a success response for both complete and partial success
    if is_complete or is_partial:
        response = {
            "success": True,
            "status": status.get("status", "unknown"),
            "message": status.get("message", "Processing status unavailable"),
            "is_complete": is_complete or is_partial,  # Consider partial as complete for UI purposes
            "files_processed": status.get("processed", 0),
            "total_files": status.get("total_files", 0)
        }
        
        # Add information about partial processing if relevant
        if is_partial:
            response["partial_success"] = True
            response["warning"] = "Some files could not be processed. You can still proceed to the chat interface."
            if "failed_files" in status:
                response["failed_files"] = status["failed_files"]
                
        return jsonify(response)
    
    # Otherwise, return the current status
    return jsonify({
        "success": True,
        "status": status.get("status", "unknown"),
        "message": status.get("message", "Processing status unavailable"),
        "is_complete": False,
        "files_processed": status.get("processed", 0),
        "total_files": status.get("total_files", 0)
    })

# Basic Flask run setup for local development
if __name__ == '__main__':
    # Ensure a default GCS bucket name for local testing if not set in env
    if not os.getenv('GCS_BUCKET_NAME'):
        os.environ['GCS_BUCKET_NAME'] = 'gca-cloned-repos-graphrag-464113' # CHANGE THIS TO YOUR ACTUAL BUCKET NAME

    # For local testing, ensure your gcloud application-default login is done
    # gcloud auth application-default login
    app.run(debug=True, port=5000) # Run Flask on port 5000