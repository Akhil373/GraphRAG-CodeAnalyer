import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, Neo4jError
import vertexai
from vertexai.language_models import TextEmbeddingModel
from vertexai.generative_models import GenerativeModel
from google.api_core.exceptions import GoogleAPIError
import re
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
app.logger.info("Flask app starting up...")

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_REGION = os.getenv("GCP_REGION")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

app.logger.info(f"Neo4j URI: {NEO4J_URI}, Username: {NEO4J_USERNAME}")

if not GCP_PROJECT_ID:
    app.logger.critical("GCP_PROJECT_ID environment variable not set. Exiting.")
    exit(1)
if not GCP_REGION:
    app.logger.critical("GCP_REGION environment variable not set. Exiting.")
    exit(1)
if not NEO4J_URI:
    app.logger.critical("NEO4J_URI environment variable not set. Exiting.")
    exit(1)
if not NEO4J_USERNAME:
    app.logger.critical("NEO4J_USERNAME environment variable not set. Exiting.")
    exit(1)
if not NEO4J_PASSWORD:
    app.logger.critical("NEO4J_PASSWORD environment variable not set. Exiting.")
    exit(1)

try:
    vertexai.init(project=GCP_PROJECT_ID, location="us-central1")
    embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-large-exp-03-07")
    generative_model = GenerativeModel("gemini-2.5-pro")
    app.logger.info(f"Vertex AI initialized for project '{GCP_PROJECT_ID}' in region '{GCP_REGION}'.")
except GoogleAPIError as e:
    app.logger.critical(f"Failed to initialize Vertex AI or load models: {e}", exc_info=True)
    exit(1)
except Exception as e:
    app.logger.critical(f"An unexpected error occurred during Vertex AI initialization: {e}", exc_info=True)
    exit(1)

neo4j_driver = None

def get_neo4j_driver():
    global neo4j_driver
    if neo4j_driver is None:
        try:
            neo4j_driver = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
            )
            neo4j_driver.verify_connectivity()
            app.logger.info("Neo4j driver initialized and connected successfully.")
            create_vector_indexes(neo4j_driver)
        except ServiceUnavailable as e:
            app.logger.critical(
                f"Neo4j connection failed: Service Unavailable. "
                f"Check URI, credentials, and network. Error: {e}",
                exc_info=True
            )
            raise ConnectionError("Failed to connect to Neo4j database.") from e
        except Exception as e:
            app.logger.critical(f"An unexpected error occurred during Neo4j driver initialization: {e}", exc_info=True)
            raise ConnectionError("Failed to initialize Neo4j database driver.") from e
    return neo4j_driver

def create_vector_indexes(driver):
    desired_dim = 3072

    index_creation_queries = {
        "file_index": f"""
            CREATE VECTOR INDEX `file_index` IF NOT EXISTS
            FOR (f:File) ON (f.embedding)
            OPTIONS {{
                indexConfig: {{
                    `vector.dimensions`: {desired_dim},
                    `vector.similarity_function`: 'cosine'
                }}
            }}
        """,
        "function_index": f"""
            CREATE VECTOR INDEX `function_index` IF NOT EXISTS
            FOR (func:Function) ON (func.embedding)
            OPTIONS {{
                indexConfig: {{
                    `vector.dimensions`: {desired_dim},
                    `vector.similarity_function`: 'cosine'
                }}
            }}
        """,
        "operation_index": f"""
            CREATE VECTOR INDEX `operation_index` IF NOT EXISTS
            FOR (op:Operation) ON (op.embedding)
            OPTIONS {{
                indexConfig: {{
                    `vector.dimensions`: {desired_dim},
                    `vector.similarity_function`: 'cosine'
                }}
            }}
        """
    }

    try:
        with driver.session() as session:
            try:
                existing_indexes = session.run("SHOW INDEXES WHERE name in ['file_index', 'function_index', 'operation_index']").data()
                
                for index in existing_indexes:
                    index_name = index.get('name')
                    if index_name:
                        app.logger.info(f"Dropping existing index: {index_name}")
                        session.run(f"DROP INDEX {index_name}")
            except Exception as e:
                app.logger.warning(f"Could not check or drop existing indexes: {e}")
                
            for index_name, create_query in index_creation_queries.items():
                app.logger.info(f"Creating vector index '{index_name}' with dimension {desired_dim}...")
                session.run(create_query)
                app.logger.info(f"Vector index '{index_name}' created successfully.")
                
    except Neo4jError as e:
        app.logger.error(
            "Failed to create or verify GDS vector index. Please ensure the GDS plugin is installed in Neo4j. Error: %s",
            e,
            exc_info=True,
        )
    except Exception as e:
        app.logger.error("An unexpected error occurred during index creation: %s", e, exc_info=True)

@app.errorhandler(400)
def handle_bad_request(e):
    app.logger.error(f"Bad Request (400): {e.description}", exc_info=True)
    return jsonify({
        "status": 400,
        "message": "Bad Request: " + getattr(e, 'description', 'Invalid request data.'),
        "error_type": "BadRequest"
    }), 400

@app.errorhandler(404)
def handle_not_found(e):
    app.logger.warning(f"Not Found (404): {request.path} was requested.", exc_info=True)
    return jsonify({
        "status": 404,
        "message": "Not Found: The requested resource does not exist.",
        "error_type": "NotFound"
    }), 404

@app.errorhandler(ConnectionError)
def handle_db_connection_error(e):
    app.logger.critical(f"Database connection error: {e}", exc_info=True)
    return jsonify({
        "status": 500,
        "message": "Internal Server Error: Could not connect to the database. Please try again later.",
        "error_type": "DatabaseConnectionError"
    }), 500

@app.errorhandler(GoogleAPIError)
def handle_google_api_error(e):
    app.logger.error(f"Google API Error: {e.reason} (Code: {e.code})", exc_info=True)
    return jsonify({
        "status": 500,
        "message": f"Internal Server Error: Google Cloud API issue. Reason: {e.reason}. Please check API permissions/quotas.",
        "error_type": "GoogleAPIError"
    }), 500

@app.errorhandler(Exception)
def handle_generic_error(e):
    app.logger.error(f"An unhandled internal server error occurred: {e}", exc_info=True)
    return jsonify({
        "status": 500,
        "message": "Internal Server Error: An unexpected error occurred. Please try again or contact support.",
        "error_type": "InternalServerError"
    }), 500


def generate_embeddings(text):
    """Generates embeddings for a given text using Vertex AI."""
    if not text:
        app.logger.warning("Attempted to generate embedding for empty text.")
        return []
    try:
        embeddings = embedding_model.get_embeddings([text])
        return embeddings[0].values
    except Exception as e:
        app.logger.error(f"Vertex AI embedding model error: {e}", exc_info=True)
        raise

def retrieve_graph_context(query_embedding, user_query, session):
    """
    Retrieves relevant context from the Neo4j graph using hybrid vector search + graph traversal.
    """
    context = []
    
    try:
        # --- Special query handling for file operations ---
        # Check if the query is asking about operations in a specific file
        file_operations_pattern = re.compile(r'what (?:operations|functions|can|does).*(?:in|with) (\w+\.[a-zA-Z]+)', re.IGNORECASE)
        file_match = file_operations_pattern.search(user_query)
        
        explain_operation_pattern = re.compile(r'explain\s+([a-zA-Z0-9_\s]+)\s+(?:operation|function|code)?\s+in\s+(\w+\.[a-zA-Z]+)', re.IGNORECASE)
        explain_match = explain_operation_pattern.search(user_query)
        
        if file_match:
            file_name = file_match.group(1)
            app.logger.info(f"Detected file operations query for: {file_name}")
            
            # Get all operations in this file
            operations_query = """
            MATCH (f:File)-[:CONTAINS_OPERATION]->(o:Operation)
            WHERE f.name = $file_name OR f.path ENDS WITH $file_name
            RETURN o.name AS name, o.description AS description, o.code_snippet AS code
            ORDER BY o.name
            """
            operations = session.run(operations_query, file_name=file_name).data()
            
            if operations:
                context.append(f"Operations available in {file_name}:")
                for i, op in enumerate(operations):
                    context.append(f"{i+1}. {op.get('description', op.get('name', 'Unnamed operation'))}")
                return "\n".join(context)
        
        # Handle "explain X operation in Y file" queries
        elif explain_match:
            operation_name = explain_match.group(1).strip().lower()
            file_name = explain_match.group(2)
            app.logger.info(f"Detected explain operation query: {operation_name} in {file_name}")
            
            # Get specific operation by description or name
            operation_query = """
            MATCH (f:File)-[:CONTAINS_OPERATION]->(o:Operation)
            WHERE (f.name = $file_name OR f.path ENDS WITH $file_name) AND
                  (toLower(o.name) CONTAINS $operation_name OR
                   toLower(o.description) CONTAINS $operation_name)
            RETURN o.name AS name, o.description AS description, o.code_snippet AS code
            LIMIT 1
            """
            operation = session.run(operation_query, file_name=file_name, operation_name=operation_name).single()
            
            if operation:
                code = operation.get('code')
                if code:
                    context.append(f"Here is the code for '{operation.get('description', operation.get('name'))}':")
                    context.append(f"```c\n{code}\n```")
                    return "\n".join(context)
            
            # Try to find any function that matches the description
            function_query = """
            MATCH (f:File)-[:CONTAINS]->(func:Function)
            WHERE (f.name = $file_name OR f.path ENDS WITH $file_name) AND
                  (toLower(func.name) CONTAINS $operation_name OR
                   toLower(func.description) CONTAINS $operation_name)
            RETURN func.name AS name, func.description AS description, func.context_sample AS code
            LIMIT 1
            """
            function = session.run(function_query, file_name=file_name, operation_name=operation_name).single()
            
            if function:
                code = function.get('code')
                if code:
                    context.append(f"Here is the function '{function.get('name')}' that matches your query:")
                    context.append(f"```c\n{code}\n```")
                    return "\n".join(context)
        
        # --- 1. Vector Search (Primary Method) ---
        app.logger.info("Executing entity vector search query...")
        
        # Search operation index first
        operation_query = """
        CALL db.index.vector.queryNodes('operation_index', 5, $query_embedding) YIELD node, score
        WHERE score > 0.6
        RETURN node.name AS name, 'Operation' as type, node.file_path AS filePath,
               node.description as description, node.code_snippet as code, score
        ORDER BY score DESC
        """
        operation_results = session.run(operation_query, query_embedding=query_embedding).data()
        if operation_results:
            app.logger.info(f"Found {len(operation_results)} relevant operations via vector search")
            
        # Search function index
        function_query = """
        CALL db.index.vector.queryNodes('function_index', 5, $query_embedding) YIELD node, score
        WHERE score > 0.6 AND node.file_path IS NOT NULL
        RETURN node.name AS name, labels(node)[0] as type, node.file_path AS filePath, 
               node.description as description, node.context_sample as code, score
        ORDER BY score DESC
        """
        function_results = session.run(function_query, query_embedding=query_embedding).data()
        
        # Search file index
        file_query = """
        CALL db.index.vector.queryNodes('file_index', 5, $query_embedding) YIELD node, score
        WHERE score > 0.6
        RETURN node.name AS name, labels(node)[0] as type, node.path AS filePath, 
               node.description as description, score, node.context_sample as code
        ORDER BY score DESC
        """
        file_results = session.run(file_query, query_embedding=query_embedding).data()
        
        # Combine results, prioritizing operations
        entity_results = operation_results + function_results + file_results
        entity_ids = [f"{res['name']}-{res['filePath']}" for res in entity_results]
        
        for res in entity_results:
            entity_type = res.get('type', 'Entity')
            file_path = res.get('filePath', 'unknown path')
            description = res.get('description', '')
            
            # Include code sample if available
            code_sample = res.get('code', '')
            if code_sample:
                if entity_type.lower() == 'operation':
                    # Format operations differently to highlight them
                    context.append(f"Operation in file '{file_path}': {description}\nCode:\n```\n{code_sample}\n```")
                else:
                    context.append(f"In file '{file_path}', there is a {entity_type.lower()} called '{res['name']}'. {description}\nCode:\n```\n{code_sample}\n```")
            else:
                context.append(f"In file '{file_path}', there is a {entity_type.lower()} called '{res['name']}'. {description}")
        
        app.logger.info(f"Found {len(entity_results)} entity contexts via vector search.")
        
        # --- 2. Graph Traversal Expansion ---
        if entity_results:
            app.logger.info("Expanding context via graph traversal...")
            traversal_query = """
            UNWIND $entityIds AS entityId
            WITH split(entityId, '-')[0] AS name, split(entityId, '-')[1] AS filePath
            MATCH (start)
            WHERE (start:Function AND start.name = name AND start.file_path = filePath)
               OR (start:File AND start.path = filePath)
            CALL apoc.path.expandConfig(start, {
                relationshipFilter: "CALLS|USES|DEFINES|CONTAINS|IMPORTS|DEPENDS_ON|READS_FROM|WRITES_TO",
                minLevel: 1,
                maxLevel: 2,
                uniqueness: "NODE_GLOBAL"
            }) YIELD path
            WITH last(nodes(path)) AS node, start
            WHERE node <> start
            RETURN DISTINCT node.name AS name, labels(node)[0] as type, 
                   coalesce(node.file_path, node.path) AS filePath,
                   node.description as description, node.context_sample as code
            LIMIT 20
            """
            traversal_results = session.run(traversal_query, entityIds=entity_ids).data()
            
            for res in traversal_results:
                entity_type = res.get('type', 'Entity')
                file_path = res.get('filePath', 'unknown path')
                description = res.get('description', '')
                code_sample = res.get('code', '')
                if code_sample:
                    context.append(f"Via graph traversal: In file '{file_path}', there is a {entity_type.lower()} called '{res['name']}'. {description}\nCode:\n```\n{code_sample}\n```")
                else:
                    context.append(f"Via graph traversal: In file '{file_path}', there is a {entity_type.lower()} called '{res['name']}'. {description}")
        
        # --- 3. Shortest Path Connections ---
        if len(entity_results) >= 2:
            app.logger.info("Finding connections between top entities...")
            top_entities = [res['name'] for res in entity_results[:2]]
            path_query = """
            MATCH (a), (b)
            WHERE a.name = $entity1 AND b.name = $entity2
            CALL apoc.algo.allSimplePaths(a, b, null, 3) YIELD path
            WITH path, length(path) AS length
            ORDER BY length ASC
            LIMIT 3
            UNWIND nodes(path) AS node
            RETURN DISTINCT node.name AS name, labels(node)[0] as type, 
                   coalesce(node.file_path, node.path) AS filePath,
                   node.description as description, node.context_sample as code
            """
            path_results = session.run(
                path_query, 
                entity1=top_entities[0], 
                entity2=top_entities[1]
            ).data()
            
            for res in path_results:
                entity_type = res.get('type', 'Entity')
                file_path = res.get('filePath', 'unknown path')
                description = res.get('description', '')
                code_sample = res.get('code', '')
                if code_sample:
                    context.append(f"Via path connection: In file '{file_path}', there is a {entity_type.lower()} called '{res['name']}'. {description}\nCode:\n```\n{code_sample}\n```")
                else:
                    context.append(f"Via path connection: In file '{file_path}', there is a {entity_type.lower()} called '{res['name']}'. {description}")
        
        # --- 4. Keyword Search (Complementary Method) ---
        # Extract keywords from the user query
        keywords = [word.lower() for word in user_query.split() if len(word) > 2]
        keywords = [word for word in keywords if word not in 
                   ['the', 'and', 'for', 'with', 'what', 'how', 'why', 'where', 'when', 'who', 'which']]
        
        if keywords:
            # Search for entities by name
            name_query = """
            MATCH (n)
            WHERE (ANY(keyword IN $keywords WHERE toLower(n.name) CONTAINS keyword))
            RETURN n.name as name, labels(n)[0] as type, n.file_path as filePath, 
                   n.description as description, n.context_sample as code
            LIMIT 5
            """
            app.logger.info("Executing keyword name search...")
            name_results = session.run(name_query, keywords=keywords).data()
            
            for res in name_results:
                entity_type = res.get('type', 'Entity')
                description = res.get('description', '')
                code_sample = res.get('code', '')
                if code_sample:
                    context.append(f"Found a {entity_type.lower()} named '{res['name']}' in '{res['filePath']}' that matches your query. {description}\nCode:\n```\n{code_sample}\n```")
                else:
                    context.append(f"Found a {entity_type.lower()} named '{res['name']}' in '{res['filePath']}' that matches your query. {description}")
            
            # Search for entities by description
            desc_query = """
            MATCH (n)
            WHERE (ANY(keyword IN $keywords WHERE toLower(n.description) CONTAINS keyword))
            RETURN n.name as name, labels(n)[0] as type, n.file_path as filePath, 
                   n.description as description, n.context_sample as code
            LIMIT 5
            """
            app.logger.info("Executing keyword description search...")
            desc_results = session.run(desc_query, keywords=keywords).data()
            
            for res in desc_results:
                entity_type = res.get('type', 'Entity')
                description = res.get('description', '')
                code_sample = res.get('code', '')
                if code_sample:
                    context.append(f"The {entity_type.lower()} '{res['name']}' in '{res['filePath']}' appears relevant to your question. {description}\nCode:\n```\n{code_sample}\n```")
                else:
                    context.append(f"The {entity_type.lower()} '{res['name']}' in '{res['filePath']}' appears relevant to your question. {description}")
        
        # --- 5. File Context ---
        # Get information about the files containing the entities
        if entity_results:
            # Collect file paths, handling both file_path and path properties
            file_paths = []
            for res in entity_results:
                if 'filePath' in res and res['filePath']:
                    file_paths.append(res['filePath'])
            
            file_paths = list(set(file_paths))  # Remove duplicates
            
            if file_paths:
                # Use a more comprehensive query to get file information
                file_query = """
                MATCH (f)
                WHERE (f:File OR f:SourceFile OR f:PythonModule OR f:JavaScriptModule OR f:CobolProgram 
                       OR f:SasProgram OR f:JclJob OR f:FlinkJob OR f:DataFile OR f:CppFile 
                       OR f:FortranProgram OR f:PliProgram OR f:AssemblyFile OR f:RpgProgram)
                AND (f.path IN $filePaths OR f.file_path IN $filePaths)
                RETURN COALESCE(f.path, f.file_path) as path, f.repo_id as repoId, labels(f) as fileLabels
                """
                app.logger.info(f"Getting file context for {len(file_paths)} files")
                file_results = session.run(file_query, filePaths=file_paths).data()
                
                for res in file_results:
                    file_labels = res.get('fileLabels', [])
                    file_type = "file"
                    # Extract the most specific file type label (not 'File')
                    for label in file_labels:
                        if label != 'File':
                            file_type = label.replace('File', '').replace('Program', '').replace('Module', '').replace('Job', '')
                            break
                    
                    context.append(f"The {file_type.lower()} file '{res['path']}' is part of repository '{res.get('repoId', 'unknown')}'.")
                    
                    # Get other entities in the same file with improved query
                    file_entities_query = """
                    MATCH (f)-[:CONTAINS]->(e)
                    WHERE f.path = $path OR f.file_path = $path
                    RETURN e.name as name, labels(e)[0] as type
                    LIMIT 8
                    """
                    file_entities = session.run(file_entities_query, path=res['path']).data()
                    
                    if file_entities:
                        entities_info = []
                        for e in file_entities:
                            e_type = e.get('type', 'Entity').lower()
                            e_name = e.get('name', '')
                            if e_name:
                                entities_info.append(f"{e_type} '{e_name}'")
                                
                        if entities_info:
                            entities_str = ", ".join(entities_info)
                            context.append(f"The file '{res['path']}' contains: {entities_str}.")

    except Neo4jError as e:
        app.logger.error(f"Neo4j Cypher error during context retrieval: {e.message}", exc_info=True)
    except Exception as e:
        app.logger.error(f"An unexpected error occurred during context retrieval: {e}", exc_info=True)

    if not context:
        return "No specific context found in the graph."
    else:
        return "\n".join(context)

def retrieve_file_specific_context(query_embedding, user_query, repo_id, file_path, session, context_json=None):
    """
    Retrieves relevant context from the Neo4j graph, focused specifically on the selected file.
    It first attempts a vector search for the most relevant code sections. If found, it returns
    a focused context. Otherwise, it falls back to a general context of all entities in the file.
    """
    context = []
    
    try:
        # --- 1. Vector Search First ---
        # Attempt to find the most relevant code snippets in the file to provide a focused context.
        vector_query = """
        MATCH (f:File {repo_id: $repo_id, path: $file_path})-[:CONTAINS]->(e)
        WHERE e:Function OR e:Class OR e:Operation
        WITH e
        CALL db.index.vector.queryNodes(CASE 
            WHEN e:Function THEN 'function_index' 
            WHEN e:Operation THEN 'operation_index'
            ELSE '' END, 
            3, $embedding) YIELD node, score
        WHERE node = e AND score > 0.7
        RETURN e, score
        ORDER BY score DESC
        LIMIT 3
        """
        
        vector_results = session.run(vector_query, 
                                     repo_id=repo_id, 
                                     file_path=file_path,
                                     embedding=query_embedding).data()
        
        # --- 2. Build Context Based on Search Results ---
        if vector_results:
            app.logger.info(f"Found {len(vector_results)} relevant sections via vector search. Building focused context.")
            context.append(f"FILE: {file_path}")
            
            # Add the general file code sample if available
            if context_json:
                try:
                    file_data = json.loads(context_json)
                    if file_data.get('context_sample'):
                        context.append(f"CODE SAMPLE:\n```{file_data.get('language', '')}\n{file_data['context_sample'][:1000]}\n```\n")
                except json.JSONDecodeError:
                    app.logger.error("Could not parse provided context JSON for focused view.")

            context.append("\nMOST RELEVANT CODE SECTIONS:")
            for result in vector_results:
                entity = result.get('e')
                score = result.get('score', 0)
                if entity:
                    entity_data = dict(entity)
                    entity_type = "Unknown"
                    if hasattr(entity, 'labels'):
                        entity_type = list(entity.labels)[0] if entity.labels else "Unknown"
                    elif isinstance(entity, dict) and entity.get('labels'):
                        entity_type = entity.get('labels')[0] if entity.get('labels') else "Unknown"
                    
                    context.append(f"\n- {entity_type}: {entity_data.get('name', 'Unnamed')} (Relevance: {score:.2f})")
                    if entity_data.get('description'):
                        context.append(f"  DESCRIPTION: {entity_data.get('description')}")
                    
                    # Get code snippet from properties based on entity type
                    if entity_type == "Function" or entity_type == "Class":
                        if entity_data.get('context_sample'):
                            context.append(f"  CODE SNIPPET:\n```\n{entity_data.get('context_sample')[:1000]}\n```\n")
                    elif entity_type == "Operation":
                        if entity_data.get('code_snippet'):
                            context.append(f"  CODE SNIPPET:\n```\n{entity_data.get('code_snippet')[:1000]}\n```\n")
            
            # Return the focused context immediately
            return "\n".join(context)

        # --- 3. Fallback to General Context ---
        # If no highly relevant snippets are found, build a general context of the file.
        else:
            app.logger.info("No high-relevance sections found via vector search. Building general file context.")
            # If context_json is provided, we can use it directly
            if context_json:
                try:
                    file_data = json.loads(context_json)
                    # Format the context data
                    context.append(f"FILE: {file_path}")
                    if file_data.get('language'):
                        context.append(f"LANGUAGE: {file_data['language']}")
                    if file_data.get('context_sample'):
                        context.append(f"CODE SAMPLE:\n```{file_data['language']}\n{file_data['context_sample'][:1000]}\n```\n")
                except json.JSONDecodeError:
                    app.logger.error("Could not parse provided context JSON")
            
            # Query for entities directly related to this file
            file_query = """
            MATCH (f:File {repo_id: $repo_id, path: $file_path})
            OPTIONAL MATCH (f)-[r]->(e)
            RETURN type(r) as relationship_type, e
            LIMIT 30
            """
            
            file_results = session.run(file_query, repo_id=repo_id, file_path=file_path).data()
            
            if file_results:
                # Group entities by relationship type
                entities_by_type = {}
                for result in file_results:
                    rel_type = result.get('relationship_type')
                    entity = result.get('e')
                    
                    if rel_type and entity:
                        if rel_type not in entities_by_type:
                            entities_by_type[rel_type] = []
                        
                        # Format the entity data
                        entity_data = dict(entity)
                        entity_type = "Unknown"
                        
                        # Check if entity is a Neo4j Node object or a dict
                        if hasattr(entity, 'labels'):
                            entity_type = list(entity.labels)[0] if entity.labels else "Unknown"
                        elif isinstance(entity, dict) and entity.get('labels'):
                            entity_type = entity.get('labels')[0] if entity.get('labels') else "Unknown"
                        
                        entities_by_type[rel_type].append({
                            "type": entity_type,
                            "data": entity_data
                        })
                
                # Format the context by relationship type
                for rel_type, entities in entities_by_type.items():
                    context.append(f"\n{rel_type.upper()} RELATIONSHIPS:")
                    for entity in entities:
                        entity_type = entity["type"]
                        entity_data = entity["data"]
                        
                        if entity_type == "Function":
                            context.append(f"- FUNCTION: {entity_data.get('name', 'Unnamed')}")
                            if entity_data.get('description'):
                                context.append(f"  DESCRIPTION: {entity_data.get('description')}")
                            if entity_data.get('params'):
                                context.append(f"  PARAMETERS: {str(entity_data.get('params'))}")
                            if entity_data.get('return_type'):
                                context.append(f"  RETURN TYPE: {str(entity_data.get('return_type'))}")
                            if entity_data.get('context_sample'):
                                context.append(f"  CODE SNIPPET:\n```\n{entity_data.get('context_sample')[:500]}\n```\n")
                        
                        elif entity_type == "Variable":
                            context.append(f"- VARIABLE: {entity_data.get('name', 'Unnamed')}")
                            if entity_data.get('description'):
                                context.append(f"  DESCRIPTION: {entity_data.get('description')}")
                            if entity_data.get('data_type'):
                                context.append(f"  TYPE: {str(entity_data.get('data_type'))}")
                        
                        elif entity_type == "Class":
                            context.append(f"- CLASS: {entity_data.get('name', 'Unnamed')}")
                            if entity_data.get('description'):
                                context.append(f"  DESCRIPTION: {entity_data.get('description')}")
                            if entity_data.get('fields'):
                                context.append(f"  FIELDS: {str(entity_data.get('fields'))}")
                            if entity_data.get('context_sample'):
                                context.append(f"  CODE SNIPPET:\n```\n{entity_data.get('context_sample')[:500]}\n```\n")
                        
                        elif entity_type == "Operation":
                            context.append(f"- OPERATION: {entity_data.get('name', 'Unnamed')}")
                            if entity_data.get('description'):
                                context.append(f"  DESCRIPTION: {entity_data.get('description')}")
                            if entity_data.get('code_snippet'):
                                context.append(f"  CODE SNIPPET:\n```\n{entity_data.get('code_snippet')[:500]}\n```\n")
                        
                        else:
                            # For other entity types
                            context.append(f"- {entity_type}: {entity_data.get('name', 'Unnamed')}")
                            if entity_data.get('description'):
                                context.append(f"  DESCRIPTION: {entity_data.get('description')}")

    except Exception as e:
        app.logger.error(f"Error retrieving file-specific context: {e}", exc_info=True)
    
    return "\n".join(context)

# --- API Endpoints ---
@app.route('/api/chat', methods=['POST'])
def chat_with_graph():
    app.logger.info("Received /api/chat request.")
    data = request.json
    user_query = data.get('query')
    conversation_history = data.get('history', [])
    repo_id = data.get('repo_id')
    file_path = data.get('file_path')
    context_json = data.get('context')
    is_repo_context = data.get('is_repo_context', False)

    if not user_query:
        app.logger.warning("Chat query received with no 'query' field.")
        return jsonify({"response": "Please provide a query."}), 400

    try:
        driver = get_neo4j_driver() # Ensure driver is connected
        app.logger.info(f"Generating embedding for user query: '{user_query[:50]}...'")
        query_embedding = generate_embeddings(user_query)

        if not query_embedding:
            app.logger.error("Failed to generate embedding for the query. Cannot proceed with RAG.")
            return jsonify({
                "response": "Could not generate embeddings for your query. Please try again.",
                "context_used": ""
            }), 500

        with driver.session() as session:
            app.logger.info("Retrieving context...")
            
            # Determine which context retrieval method to use based on the request
            if is_repo_context:
                app.logger.info(f"Using repository-wide context for repo_id: {repo_id}")
                graph_context = retrieve_graph_context(query_embedding, user_query, session)
            elif file_path and repo_id:
                app.logger.info(f"Using file-specific context for {file_path}")
                graph_context = retrieve_file_specific_context(query_embedding, user_query, repo_id, file_path, session, context_json)
            else:
                app.logger.info("Using general graph context as fallback")
                graph_context = retrieve_graph_context(query_embedding, user_query, session)
                
            app.logger.info(f"Context retrieved: {'Context found' if graph_context else 'No context found'}")

            # Print the human-readable context to the terminal
            print("\n===== Context sent to Gemini =====\n")
            print(graph_context)
            print("\n==================================================\n")

        # Format conversation history for the prompt
        conversation_context = ""
        if conversation_history:
            conversation_context = "Previous conversation:\n"
            for msg in conversation_history[-5:]:  # Only include the last 5 messages
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                conversation_context += f"{role.capitalize()}: {content}\n"
            
        # Customize the prompt based on context mode
        if is_repo_context:
            prompt = f"""
            You are a codebase expert assistant. Provide detailed technical explanations about the entire repository using ONLY the context below.
            Response guidelines:
            - Keep responses concise and under 300 words total
            - Be direct and focused on answering exactly what was asked
            - Focus on the repository's architecture, key components, and relationships between files
            - Provide cross-file insights and high-level understanding
            - Include only the most important implementation details
            - Never add disclaimers or conversational fluff
            - If referring to code snippets, always include them in a code block
            - Format code as:
              ```
              // The actual code snippet being discussed
              ```
            
            {conversation_context}
            
            User Question: {user_query}
            
            Repository Context:
            ---
            {graph_context}
            ---
            """
        elif file_path:
            prompt = f"""
            You are a codebase expert assistant. Provide detailed technical explanations about the file {file_path} using ONLY the context below.
            Response guidelines:
            - Keep responses concise and under 200 words total
            - Be direct and focused on answering exactly what was asked
            - Focus specifically on the selected file's code functionality, relationships, and structure
            - Only refer to entities that are directly related to this file
            - Include only the most important implementation details
            - Never add disclaimers or conversational fluff
            - If referring to code snippets, always include them in a code block
            - Format code as:
              ```
              // The actual code snippet being discussed
              ```
            
            {conversation_context}
            
            User Question: {user_query}
            
            Context about file {file_path}:
            ---
            {graph_context}
            ---
            """
        else:
            prompt = f"""
            You are a codebase expert assistant. Provide detailed technical explanations using ONLY the context below.
            Response guidelines:
            - Keep responses concise and under 200 words total
            - Be direct and focused on answering exactly what was asked
            - Focus on code functionality, relationships, and structure
            - Include only the most important implementation details
            - Never add disclaimers or conversational fluff
            - ALWAYS start your response with the relevant code snippet in a code block
            - Format explanations as:
                ```language
                // The actual code snippet being discussed
                ```
                
                [File] → [Entity]: (IMPORTANT: Use only the base filename without any path, e.g. "main.py → function_name" not "cloned_repos/xyz/main.py → function_name")
                - Purpose: [Concise purpose]
                - Implementation: [Key technical details]
                - Relationships: [Connections to other entities]
            
            {conversation_context}
            
            User Question: {user_query}
            
            Context from Knowledge Graph:
            ---
            {graph_context}
            ---
            """
            
        app.logger.info("Calling Generative Model (Gemini)...")
        response = generative_model.generate_content(
            prompt,
            generation_config={
                # "max_output_tokens": 600,  # Increased for technical depth
                "temperature": 0.3         # Balanced creativity
            }
        )
        app.logger.info("Gemini response received.")

        return jsonify({
            "success": True,
            "response": response.text,
            "context_used": graph_context
        })

    except ConnectionError:
        app.logger.error("Chat API failed due to database connection issue.", exc_info=True)
        raise
    except GoogleAPIError:
        app.logger.error("Chat API failed due to Google API issue.", exc_info=True)
        raise
    except Exception as e:
        app.logger.error(f"An unexpected error occurred in /api/chat: {e}", exc_info=True)
        raise

@app.route('/healthz', methods=['GET'])
def health_check():
    """
    Health check endpoint to verify the service is running and can connect to critical dependencies.
    """
    try:
        # Try to get Neo4j driver (will attempt connection if not already connected)
        driver = get_neo4j_driver()
        # Verify connectivity of the driver
        driver.verify_connectivity()
        app.logger.info("Health check: Neo4j connection OK.")

        # Try to make a dummy call to a Vertex AI model (e.g., embedding a small string)
        # This checks if the service account has permissions and API is accessible
        try:
            embedding_model.get_embeddings(["health check test"])
            app.logger.info("Health check: Vertex AI embeddings OK.")
        except Exception as ve:
            app.logger.error(f"Health check: Vertex AI embeddings FAILED: {ve}", exc_info=True)
            return jsonify({"status": "ERROR", "message": f"Vertex AI embeddings service unavailable: {ve}"}), 503

        # If both are successful
        return jsonify({"status": "OK", "message": "Service is healthy and connected to dependencies."}), 200
    except ConnectionError as e:
        app.logger.error(f"Health check: Neo4j connection FAILED: {e}", exc_info=True)
        return jsonify({"status": "ERROR", "message": f"Neo4j database connection failed: {e}"}), 503
    except Exception as e:
        app.logger.error(f"Health check: An unexpected error occurred during health check: {e}", exc_info=True)
        return jsonify({"status": "ERROR", "message": f"Unexpected health check error: {e}"}), 500

@app.route('/api/clear-database', methods=['POST'])
def clear_database():
    """
    Clear all data from the Neo4j database.
    This endpoint is used when a user wants to start fresh with a new codebase.
    """
    app.logger.info("Received request to clear the Neo4j database")
    
    try:
        driver = get_neo4j_driver()
        
        with driver.session() as session:
            # Delete all relationships first, then all nodes
            app.logger.info("Deleting all relationships in the database")
            session.run("MATCH ()-[r]-() DELETE r")
            
            app.logger.info("Deleting all nodes in the database")
            session.run("MATCH (n) DELETE n")
            
            app.logger.info("Database cleared successfully")
            
        return jsonify({
            "success": True,
            "message": "Database cleared successfully"
        })
        
    except ConnectionError:
        app.logger.error("Failed to clear database due to connection issue", exc_info=True)
        raise
    except Neo4jError as e:
        app.logger.error(f"Neo4j error while clearing database: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Database error: {str(e)}"
        }), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred while clearing database: {e}", exc_info=True)
        return jsonify({
            "success": False, 
            "message": f"Unexpected error: {str(e)}"
        }), 500
@app.route('/api/graph/files', methods=['GET'])
@app.route('/graph/files', methods=['GET'])
def get_files():
    """Get all files from Neo4j for a specific repo"""
    repo_id = request.args.get('repo_id')
    
    if not repo_id:
        return jsonify({'success': False, 'message': 'Repository ID is required'}), 400
        
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            # Debug: First check what file nodes exist
            debug_query = """
            MATCH (f:File) 
            RETURN f.repo_id, f.path, f.name, labels(f) as labels
            LIMIT 10
            """
            debug_result = session.run(debug_query).data()
            app.logger.info(f"Debug - Found {len(debug_result)} file nodes: {debug_result}")
            
            # Try multiple query patterns to find files
            queries_to_try = [
                # Original query
                """
                MATCH (f:File {repo_id: $repo_id})
                RETURN f.path AS path, f.name AS name
                ORDER BY f.path
                """,
                # Try with file_path property instead of path
                """
                MATCH (f:File {repo_id: $repo_id})
                RETURN COALESCE(f.path, f.file_path) AS path, f.name AS name
                ORDER BY path
                """,
                # Try matching repo_id as substring
                """
                MATCH (f:File) 
                WHERE f.repo_id CONTAINS $repo_id OR $repo_id CONTAINS f.repo_id
                RETURN COALESCE(f.path, f.file_path) AS path, f.name AS name
                ORDER BY path
                """,
                # Try with different file type labels
                """
                MATCH (f) 
                WHERE (f:File OR f:SourceFile OR f:PythonModule OR f:DataFile) 
                AND (f.repo_id = $repo_id OR f.repo_id CONTAINS $repo_id OR $repo_id CONTAINS f.repo_id)
                RETURN COALESCE(f.path, f.file_path) AS path, COALESCE(f.name, f.path) AS name
                ORDER BY path
                """
            ]
            
            files = []
            for i, query in enumerate(queries_to_try):
                try:
                    result = session.run(query, repo_id=repo_id).data()
                    app.logger.info(f"Query {i+1} returned {len(result)} results")
                    if result:
                        files = [{'path': file['path'], 'name': file['name']} for file in result if file['path']]
                        break
                except Exception as e:
                    app.logger.warning(f"Query {i+1} failed: {e}")
            
            return jsonify({
                'success': True,
                'files': files
            })
            
    except Exception as e:
        app.logger.error(f"Error retrieving files from Neo4j: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f"Failed to retrieve files: {str(e)}"
        }), 500
        
@app.route('/api/graph/file-graph', methods=['GET'])
@app.route('/graph/file-graph', methods=['GET'])
def get_file_graph():
    """Get graph data for visualization of a specific file and its relationships"""
    repo_id = request.args.get('repo_id')
    file_path = request.args.get('file_path')
    
    if not repo_id or not file_path:
        return jsonify({'success': False, 'message': 'Repository ID and file path are required'}), 400
    
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            # Query for file and its neighborhood (2 hops)
            query = """
            MATCH (file:File {repo_id: $repo_id, path: $file_path})
            CALL {
                WITH file
                MATCH (file)-[r1]-(n1)
                OPTIONAL MATCH (n1)-[r2]-(n2)
                WHERE n2 <> file
                RETURN n1, r1, n2, r2
            }
            RETURN 
                collect(DISTINCT file) + collect(DISTINCT n1) + collect(DISTINCT n2) AS nodes,
                collect(DISTINCT r1) + collect(DISTINCT r2) AS relationships
            """
            result = session.run(query, repo_id=repo_id, file_path=file_path).single()
            
            if not result:
                return jsonify({
                    'success': False,
                    'message': 'File not found or has no relationships'
                }), 404
            
            # Process nodes
            nodes = []
            node_ids = set()
            
            for node in result['nodes']:
                if node and node.id not in node_ids:
                    node_ids.add(node.id)
                    node_data = dict(node.items())
                    labels = list(node.labels)
                    
                    # Use the primary label as the node type
                    node_type = labels[0] if labels else 'Unknown'
                    
                    # Create a good display label
                    if 'name' in node_data:
                        label = node_data['name']
                    elif 'path' in node_data:
                        label = node_data['path'].split('/')[-1]
                    else:
                        label = f"Node-{node.id}"
                    
                    nodes.append({
                        'id': str(node.id),
                        'label': label,
                        'type': node_type,
                        'properties': node_data
                    })
            
            # Process relationships
            links = []
            rel_ids = set()
            
            for rel in result['relationships']:
                if rel and rel.id not in rel_ids:
                    rel_ids.add(rel.id)
                    links.append({
                        'id': str(rel.id),
                        'source': str(rel.start_node.id),
                        'target': str(rel.end_node.id),
                        'type': rel.type,
                        'label': rel.type.replace('_', ' ')
                    })
            
            return jsonify({
                'success': True,
                'graphData': {
                    'nodes': nodes,
                    'links': links
                }
            })
    
    except Exception as e:
        app.logger.error(f"Error retrieving graph data from Neo4j: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f"Failed to retrieve graph data: {str(e)}"
        }), 500

# --- Main Execution Block ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.logger.info(f"Starting Flask app on port {port}...")

    # For local testing, you must ensure all environment variables are set!
    # Example for Linux/macOS:
    # export GCP_PROJECT_ID="your-gcp-project-id"
    # export GCP_REGION="your-gcp-region"
    # export NEO4J_URI="neo4j+s://your-auradb-uri.databases.neo4j.io:7687"
    # export NEO4J_USERNAME="neo4j"
    # export NEO4J_PASSWORD="your_auradb_password"
    # python app.py

    # The app.run() for local development. In production, use gunicorn.
    app.run(host='0.0.0.0', port=port, debug=False) # Keep debug=False for more realistic error handling
