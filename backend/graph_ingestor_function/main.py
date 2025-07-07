import os
import json
import re
from google.cloud import storage
from neo4j import GraphDatabase
import vertexai
from vertexai.language_models import TextEmbeddingModel
from vertexai.generative_models import GenerativeModel

# Initialize clients (globally for better performance in Cloud Functions)
storage_client = storage.Client()
vertexai.init(project=os.environ.get('GCP_PROJECT_ID'), location=os.environ.get('GCP_REGION'))
embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-large-exp-03-07")

# Neo4j AuraDB connection details (get these from Aura Console)
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Create a Neo4j driver instance (re-use across invocations if possible in CF)
# In Cloud Functions, a global driver is reused across "warm" invocations.
neo4j_driver = None
def get_neo4j_driver():
    global neo4j_driver
    if neo4j_driver is None:
        if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
            raise ValueError("Neo4j credentials are not set in environment variables.")
        
        # Ensure we have proper strings for all connection parameters
        uri = str(NEO4J_URI) if NEO4J_URI is not None else ""
        username = str(NEO4J_USERNAME) if NEO4J_USERNAME is not None else ""
        password = str(NEO4J_PASSWORD) if NEO4J_PASSWORD is not None else ""
        
        if not uri or not username or not password:
            raise ValueError("Neo4j credentials cannot be empty.")
            
        neo4j_driver = GraphDatabase.driver(uri, auth=(username, password))
        # Optional: Verify connectivity on first creation
        neo4j_driver.verify_connectivity()
        print("Neo4j driver initialized and connected.")
    return neo4j_driver

def generate_embeddings(text):
    """Generates embeddings for a given text using Vertex AI."""
    try:
        embeddings = embedding_model.get_embeddings([text])
        return embeddings[0].values
    except Exception as e:
        print(f"Error generating embedding for text: '{text[:50]}...': {e}")
        return [] # Return empty list on failure

def ingest_data_to_neo4j(parsed_data, session):
    """
    Ingests parsed data from the enhanced CodeParser into Neo4j.
    Handles more detailed entity properties and relationships.
    """
    repo_id = parsed_data.get('repo_id')
    filename = parsed_data.get('filename')
    entities = parsed_data.get('entities', [])
    relationships = parsed_data.get('relationships', [])
    context_sample = parsed_data.get('context_sample', '')

    if not filename:
        print("Skipping ingestion: filename is missing from parsed data.")
        return

    # Determine file type based on extension
    file_extension = os.path.splitext(filename)[1].lower()
    
    # Classify file type - expanded to support legacy code files
    file_type = "SourceFile"
    
    # Modern languages
    if file_extension in ['.py']:
        file_type = "PythonModule"
    elif file_extension in ['.js', '.jsx', '.ts', '.tsx']:
        file_type = "JavaScriptModule"
    elif file_extension in ['.java']:
        file_type = "JavaClass" 
    elif file_extension in ['.c', '.cpp', '.cc', '.cxx']:
        file_type = "CppFile"
    
    # Legacy languages
    elif file_extension in ['.cob', '.cbl', '.cpy']:
        file_type = "CobolProgram"
    elif file_extension in ['.sas']:
        file_type = "SasProgram"
    elif file_extension in ['.jcl']:
        file_type = "JclJob"
    elif file_extension in ['.flink', '.flk']:
        file_type = "FlinkJob"
    elif file_extension in ['.rpg', '.rpgle']:
        file_type = "RpgProgram"
    elif file_extension in ['.pli', '.pl1']:
        file_type = "PliProgram"
    elif file_extension in ['.asm', '.s', '.S']:
        file_type = "AssemblyFile"
    elif file_extension in ['.for', '.f', '.f77', '.f90']:
        file_type = "FortranProgram"
    
    # Data files
    elif file_extension in ['.html', '.xml', '.json', '.yaml', '.yml', '.csv', '.dat']:
        file_type = "DataFile"
    
    # Create the File node with context sample
    session.run(
        f"MERGE (f:{file_type}:File {{path: $filename}}) " 
        "SET f.repo_id = $repo_id, f.name = $file_name, "
        "f.extension = $extension, f.context_sample = $context_sample",
        filename=filename, 
        repo_id=repo_id, 
        file_name=os.path.basename(filename), 
        extension=file_extension,
        context_sample=context_sample
    )

    # Track import files for later processing
    import_entities = []

    # Comprehensive mapping of entity types based on user requirements
    label_mapping = {
        # Basic code elements
        'function': 'Function',
        'method': 'Function',
        'variable': 'Variable',
        'struct': 'Struct',
        'record': 'Record',
        'type': 'Type',
        'module': 'Module',
        'file': 'Module',
        'class': 'Class',
        'object': 'Object',
        
        # Database related
        'database_table': 'DatabaseTable',
        'entity': 'Entity',
        
        # External interactions
        'external_api': 'ExternalAPI',
        'service': 'Service',
        
        # Business logic
        'business_rule': 'BusinessRule',
        'requirement': 'Requirement',
        
        # Control flow
        'loop': 'Loop',
        'branch': 'Branch',
        
        # I/O operations
        'input_operation': 'InputOperation',
        'output_operation': 'OutputOperation',
        'user_input': 'UserInput',
        
        # Execution units
        'job': 'Job',
        'script': 'Script',
        'program': 'Program',
        
        # Additional common types
        'constant': 'Constant',
        'interface': 'Interface',
        'import': 'Import',
        'paragraph': 'Paragraph',
        'enum': 'Enum',
        'define': 'Define'
    }

    # Ingest all entities with improved labeling and property handling
    for entity in entities:
        entity_name = entity.get('name')
        # Use the standardized entity_type field from the improved parser
        entity_type = entity.get('entity_type', 'Entity') 
        description = entity.get('description', '')
        
        # Handle imports differently
        if entity_type.lower() == 'import':
            import_entities.append(entity)
            continue
            
        # Ensure entity_type is a string before using lower()
        entity_type_str = str(entity_type).lower() if entity_type else 'entity'
        node_label = label_mapping.get(entity_type_str, entity_type_str.capitalize())
        
        # Ensure label is a valid identifier
        if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', node_label):
            node_label = 'Entity'

        # Generate embedding for the description
        embedding = generate_embeddings(description)
        
        # Extract properties from the enhanced parser output
        properties = entity.get('properties', {})
        property_cypher = ""
        property_params = {
            'file_path': entity.get('file_path', filename),
            'name': entity_name,
            'description': description,
            'embedding': embedding,
            'repo_id': repo_id
        }
        
        # Add context_sample (code snippet) if available
        context_sample = properties.get('context_sample', '')
        if not context_sample and 'code' in entity:
            context_sample = entity.get('code', '')
        if context_sample:
            property_params['context_sample'] = context_sample
            property_cypher += ", e.context_sample = $context_sample"
        
        # Add original_name if available
        if properties.get('original_name'):
            property_params['original_name'] = properties.get('original_name')
            property_cypher += ", e.original_name = $original_name"
        
        # Add source_file if available 
        if properties.get('source_file'):
            property_params['source_file'] = properties.get('source_file')
            property_cypher += ", e.source_file = $source_file"
        
        # Add enhanced properties to Neo4j
        for prop_key, prop_value in properties.items():
            # Skip already handled properties
            if prop_key in ['original_name', 'source_file']:
                continue
                
            # Skip null values and ensure property names are valid
            prop_key_str = str(prop_key)
            if prop_value is not None and re.match(r'^[A-Za-z][A-Za-z0-9_]*$', prop_key_str):
                # For array properties, convert to JSON string to store in Neo4j
                if isinstance(prop_value, (list, dict)):
                    prop_value = json.dumps(prop_value)
                property_cypher += f", e.{prop_key_str} = ${prop_key_str}"
                property_params[prop_key_str] = prop_value

        cypher = f"""
        MATCH (f:{file_type} {{path: $file_path}})
        MERGE (e:{node_label} {{name: $name, file_path: $file_path}})
        ON CREATE SET e.description = $description, e.embedding = $embedding, e.repo_id = $repo_id{property_cypher}
        ON MATCH SET e.description = $description, e.embedding = $embedding, e.repo_id = $repo_id{property_cypher}
        MERGE (f)-[:CONTAINS]->(e)
        """
        
        session.run(cypher, property_params)

    # Process imports after all entities are created
    for import_entity in import_entities:
        import_name = import_entity.get('name')
        properties = import_entity.get('properties', {})
        is_standard_library = properties.get('is_standard_library', False)
        source_file = properties.get('source_file', os.path.basename(filename))
        
        # Check if the imported file exists in our database
        result = session.run(
            """
            MATCH (f) 
            WHERE f.name = $import_name OR f.path ENDS WITH $import_name
            RETURN f.path as path, f.name as name, count(f) as count
            """,
            {"import_name": import_name}
        ).single()
        
        if result and result["count"] > 0:
            # Found existing file - create relationship to it
            target_file_path = result["path"]
            target_file_name = result["name"]
            
            print(f"Creating import relationship: {source_file} imports {target_file_name}")
            session.run(
                """
                MATCH (source:{file_type} {name: $source_file, repo_id: $repo_id})
                MATCH (target) 
                WHERE target.path = $target_path
                MERGE (source)-[r:IMPORTS]->(target)
                SET r.context = $context
                """.format(file_type=file_type),
                {
                    "source_file": source_file,
                    "target_path": target_file_path,
                    "context": f"File {source_file} imports {target_file_name}",
                    "repo_id": repo_id
                }
            )
        else:
            # Create placeholder node for the imported file
            import_file_type = "ExternalModule"
            if is_standard_library:
                import_file_type = "StandardLibrary"
                
            print(f"Creating placeholder for import: {import_name} (type: {import_file_type})")
            
            # Create the placeholder node
            session.run(
                f"""
                MERGE (imp:{import_file_type} {{name: $import_name}})
                ON CREATE SET imp.description = $description, imp.is_placeholder = true
                """,
                {
                    "import_name": import_name,
                    "description": f"External module {import_name} imported by {source_file} but not available in the repository"
                }
            )
            
            # Create relationship to the placeholder
            session.run(
                """
                MATCH (source:{file_type} {name: $source_file, repo_id: $repo_id})
                MATCH (target) 
                WHERE target.name = $import_name AND target.is_placeholder = true
                MERGE (source)-[r:IMPORTS]->(target)
                SET r.context = $context
                """.format(file_type=file_type),
                {
                    "source_file": source_file,
                    "import_name": import_name,
                    "context": f"File {source_file} imports external module {import_name}",
                    "repo_id": repo_id
                }
            )

    # Comprehensive mapping of relationship types based on user requirements
    rel_mapping = {
        # Function relationships
        'calls': 'CALLS',
        'returns': 'RETURNS',
        
        # Module relationships
        'defines': 'DEFINES',
        'includes': 'INCLUDES',
        'imports': 'IMPORTS',
        'depends_on': 'DEPENDS_ON',
        
        # Variable relationships
        'declares': 'DECLARES',
        'uses': 'USES',
        'assigns': 'ASSIGNS',
        
        # I/O relationships
        'reads_from': 'READS_FROM',
        'writes_to': 'WRITES_TO',
        'interacts_with': 'INTERACTS_WITH',
        'logs_to': 'LOGS_TO',
        
        # Type relationships
        'composes': 'COMPOSES',
        'contains': 'CONTAINS',
        
        # OOP relationships
        'extends': 'EXTENDS',
        'inherits': 'INHERITS_FROM',
        
        # Execution relationships
        'executes': 'EXECUTES',
        'calls_with_input_from': 'CALLS_WITH_INPUT_FROM',
        'satisfies': 'SATISFIES',
        'triggered_by': 'TRIGGERED_BY',
        
        # Control flow relationships
        'controls_flow_to': 'CONTROLS_FLOW_TO',
        
        # Concurrency relationships
        'spawns': 'SPAWNS',
        
        # Memory management
        'allocates': 'ALLOCATES',
        'deallocates': 'DEALLOCATES',
        
        # Legacy relationships for backward compatibility
        'implements': 'IMPLEMENTS',
        'overrides': 'OVERRIDES'
    }

    # Ingest all relationships with improved type handling and support for unique names
    for rel in relationships:
        source_name = rel.get('source')
        target_name = rel.get('target')
        rel_type = rel.get('relationship_type', 'RELATED_TO')
        
        # Skip import relationships as they're handled above
        if rel_type.lower() == 'imports':
            continue
        
        # Ensure rel_type is a string before using lower()
        rel_type_str = str(rel_type).lower() if rel_type else 'related_to'
        rel_type_upper = rel_mapping.get(rel_type_str, rel_type_str).upper()
        
        # Sanitize rel_type to prevent Cypher injection
        sanitized_rel_type = re.sub(r'[^A-Z_]', '', rel_type_upper)

        if not all([source_name, target_name, sanitized_rel_type]):
            continue

        # Improved relationship creation with better matching
        # This works with the unique entity names
        cypher = f"""
        MATCH (source {{name: $source_name, file_path: $filename}})
        MATCH (target {{name: $target_name, file_path: $filename}})
        MERGE (source)-[r:{sanitized_rel_type}]->(target)
        SET r.context = $context
        """
        
        session.run(cypher, {
            'source_name': source_name,
            'target_name': target_name,
            'filename': filename,
            'context': rel.get('context', '')
        })

    print(f"Ingested data for {filename} into Neo4j with enhanced entity and relationship handling.")


def graph_ingestor_entrypoint(event, context):
    """
    Cloud Function entry point for graph ingestion.
    Triggered by new parsed data uploads to the parsed data bucket.
    """
    bucket_name = event['bucket']
    file_name = event['name']

    print(f"Processing parsed file: {file_name} from bucket: {bucket_name}")

    if not file_name.endswith('.json'):
        print(f"Skipping non-JSON file: {file_name}")
        return

    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        
        # Get metadata from the original file if available
        metadata = blob.metadata or {}
        repo_id_from_metadata = metadata.get('repo_id')
        
        parsed_data_content = blob.download_as_text()
        parsed_data = json.loads(parsed_data_content)
        
        # Override repo_id with metadata if available
        if repo_id_from_metadata:
            parsed_data['repo_id'] = repo_id_from_metadata
            print(f"Using repo_id from metadata: {repo_id_from_metadata}")
        else:
            print(f"No repo_id in metadata, using from content: {parsed_data.get('repo_id', 'unknown')}")

        driver = get_neo4j_driver()
        with driver.session() as session:
            ingest_data_to_neo4j(parsed_data, session)

    except Exception as e:
        print(f"Error ingesting {file_name}: {e}")
        # Log the error for debugging in Cloud Logging
        raise # Re-raise to indicate function failure