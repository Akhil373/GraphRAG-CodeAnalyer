import functions_framework
import logging
import os
from google.cloud import storage
import json
from typing import List, Tuple, Dict, Any
import re
from vertexai.generative_models import GenerativeModel
import vertexai

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Data Classes for Code Structure ---

class CodeEntity:
    def __init__(self, name: str, entity_type: str, file_path: str, description: str = "", properties: Dict[str, Any] = None):
        self.name = name
        self.entity_type = entity_type
        self.file_path = file_path
        self.description = description
        self.properties = properties if properties is not None else {}
    
    def to_dict(self):
        result = self.__dict__.copy()
        # Only include properties if they exist and aren't empty
        if not self.properties:
            del result['properties']
        return result

class CodeRelationship:
    def __init__(self, source: str, target: str, relationship_type: str, context: str = ""):
        self.source = source
        self.target = target
        self.relationship_type = relationship_type
        self.context = context

    def to_dict(self):
        return self.__dict__

# --- The Core Parsing Logic ---

class CodeParser:
    def __init__(self):
        # Initialize Vertex AI
        project_id = os.environ.get('GCP_PROJECT_ID')
        # Location must be set for Vertex AI to initialize correctly
        location = os.environ.get('GCP_REGION', 'us-central1')
        vertexai.init(project=project_id, location=location)
        self.model = GenerativeModel('gemini-1.5-flash')

        # Language detection patterns - expanded with more languages
        self.language_patterns = {
            # Legacy languages
            'cobol': [r'\.cob$', r'\.cbl$', r'\.cpy$', r'IDENTIFICATION\s+DIVISION', r'PROGRAM-ID', r'PROCEDURE\s+DIVISION', r'DATA\s+DIVISION'],
            'jcl': [r'\.jcl$', r'//\w+\s+JOB', r'//\w+\s+EXEC', r'//\w+\s+DD', r'//SYSOUT'],
            'sas': [r'\.sas$', r'proc\s+\w+', r'data\s+\w+', r'run;', r'libname\s+', r'%macro', r'%mend'],
            'rpg': [r'\.rpg$', r'\.rpgle$', r'dcl-proc', r'dcl-f', r'dcl-ds', r'dcl-c', r'ctl-opt'],
            'flink': [r'\.flink$', r'\.flk$', r'StreamExecutionEnvironment', r'DataStream', r'createLocalEnvironment'],
            'fortran': [r'\.for$', r'\.f$', r'\.f77$', r'\.f90$', r'program\s+\w+', r'subroutine\s+\w+', r'function\s+\w+', r'end\s+program'],
            'pli': [r'\.pli$', r'\.pl1$', r'PROCEDURE\s+OPTIONS', r'DECLARE', r'END;'],
            'assembly': [r'\.asm$', r'\.s$', r'\.S$', r'section\s+\.text', r'global\s+_start', r'\.data', r'\.bss'],
            
            # Modern languages
            'c': [r'\.c$', r'\.h$', r'#include\s*<', r'int\s+main\s*\('],
            'cpp': [r'\.cpp$', r'\.cc$', r'\.cxx$', r'\.hpp$', r'#include\s*<iostream>', r'using\s+namespace'],
            'python': [r'\.py$', r'import\s+', r'def\s+', r'class\s+'],
            'java': [r'\.java$', r'public\s+class', r'import\s+java\.'],
            'javascript': [r'\.js$', r'const\s+', r'let\s+', r'function\s+', r'export\s+'],
            'typescript': [r'\.ts$', r'\.tsx$', r'interface\s+', r'type\s+', r'export\s+'],
            'csharp': [r'\.cs$', r'namespace\s+', r'using\s+System', r'public\s+class'],
            'go': [r'\.go$', r'package\s+', r'import\s+\(', r'func\s+'],
            'ruby': [r'\.rb$', r'require\s+', r'def\s+', r'class\s+'],
            'php': [r'\.php$', r'\<\?php', r'function\s+', r'class\s+']
        }

    def detect_language(self, file_path: str, content: str) -> str:
        """Detect programming language from file path and content."""
        for language, patterns in self.language_patterns.items():
            score = 0
            for pattern in patterns:
                if re.search(pattern, file_path, re.IGNORECASE):
                    score += 2
                if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
                    score += 1
            if score >= 2:
                return language
        return 'unknown'

    def parse_content(self, file_path: str, content: str) -> Tuple[List[CodeEntity], List[CodeRelationship], str]:
        """Parse the content of a single file."""
        language = self.detect_language(file_path, content)
        logger.info(f"Detected language for {file_path}: {language}")
        
        # Store the first 2000 characters as context sample
        context_sample = content[:2000]
        
        ai_entities, ai_relationships = self.extract_with_ai(content, language, file_path)
        
        if not ai_entities:
            logger.info(f"AI returned no entities for {file_path}, falling back to regex.")
            regex_entities, regex_relationships = self.extract_with_regex(content, language, file_path)
            return regex_entities, regex_relationships, context_sample
        
        return ai_entities, ai_relationships, context_sample

    def extract_with_ai(self, content: str, language: str, file_path: str) -> Tuple[List[CodeEntity], List[CodeRelationship]]:
        """Extract entities and relationships using Vertex AI."""
        try:
            # Enhanced prompt with more specific instructions and property extraction
            prompt = f"""
            Analyze this {language} code and extract detailed information in a structured format.

            ANALYSIS TASKS:
            1. Functions/methods: Identify name, parameters, return types, visibility, complexity, and purpose
            2. Classes/interfaces: Identify name, fields, methods, parent classes/interfaces, and purpose
            3. Variables/constants: Identify name, data type, scope, initialization value, and purpose
            4. Control flow: Identify loops, conditionals, error handling, and their relationships
            5. Dependencies: Identify external imports, includes, or library usage
            6. Architectural patterns: Identify design patterns or architectural styles if present
            7. Relationships: Identify calls, inheritance, usage, containment between components

            ENTITY TYPES TO IDENTIFY (assign each entity to exactly one of these types):
            * Function: Named procedures or subroutines in code
            * Variable: Local, global, struct/class member variables
            * Struct/Record/Type: User-defined types or records
            * Module/File: Source code files or logical modules
            * Class/Object: Object-oriented components
            * DatabaseTable/Entity: DB tables or files representing structured data
            * ExternalAPI/Service: External endpoints or services the code interacts with
            * BusinessRule/Requirement: Inferred high-level logical conditions
            * Loop/Branch: Control flow structures
            * InputOperation/OutputOperation: File reads/writes, console input, etc.
            * UserInput: Points where user input is captured
            * Job/Script/Program: Main unit of execution

            RELATIONSHIP TYPES TO IDENTIFY:
            * calls: Function → Function — A calls B
            * defines: Module → Function/Variable/Type — Defined in
            * declares: Function → Variable — Declares var
            * uses: Function → Variable — Uses in logic
            * assigns: Function → Variable — Assigns value
            * depends_on: Module/Function → Module/Function — Imports/calls
            * reads_from: Function → Input Source/Table — File/db read
            * writes_to: Function → Output Target/Table — File/db write
            * interacts_with: Function → API/Service — HTTP call, socket, etc.
            * returns: Function → Type/Value — Return type/value
            * composes: Type → Variable — Type composition
            * contains: Type → Field/StructMember — Has-a relationship
            * executes: Job/Script → Module/Function — Entry point
            * calls_with_input_from: Function → User Input — Traced input
            * satisfies: Function/Module → Requirement — Implements business rule
            * triggered_by: Function → Event/Input — Reactive code
            * controls_flow_to: Loop/Branch → Function/Block — Conditional logic
            * spawns: Function → Thread/Process — Concurrency
            * includes: File/Module → File/Module — Header or INCLUDE
            * imports: Module → Module/Package — Import or dependency
            * extends/inherits: Class → Class — Inheritance (OOP)
            * logs_to: Function → Logging Mechanism — Output for monitoring
            * allocates: Function → Memory — Memory allocation operations
            * deallocates: Function → Memory — Memory deallocation operations

            CODE TO ANALYZE:
            ```
            {content[:8000]}
            ```

            REQUIRED OUTPUT FORMAT:
            Return a properly formatted JSON object with these exact keys:
            {{
                "entities": [
                    {{
                        "name": "entity_name", 
                        "entity_type": "function|variable|struct|module|class|...", 
                        "description": "Detailed purpose of this component",
                        "properties": {{
                            "params": ["param1:type1", "param2:type2"],  // For functions/methods
                            "return_type": "return_type",                // For functions/methods
                            "visibility": "public|private|protected",    // For functions/classes/fields
                            "data_type": "type",                        // For variables/constants
                            "parent_class": "name",                     // For classes (inheritance)
                            "interfaces": ["name1", "name2"],           // For classes (implementation)
                            "fields": ["field1:type1", "field2:type2"], // For classes/structs
                            "initializer": "value",                     // For variables/constants
                            "complexity": "low|medium|high",            // For functions (optional)
                            "line_number": 42,                          // Starting line if identifiable
                            "code_length": 10                           // Length in lines if identifiable
                        }}
                    }}
                ],
                "relationships": [
                    {{
                        "source": "source_entity_name", 
                        "target": "target_entity_name", 
                        "relationship_type": "calls|defines|declares|uses|assigns|...", 
                        "context": "Detailed description of this relationship"
                    }}
                ]
            }}
            
            Be precise and thorough. Include all significant code elements. Ensure JSON is correctly formatted.
            IMPORTANT: Every entity must have a name, entity_type, and description field. The properties field should contain additional details specific to the entity type.
            """
            
            response = self.model.generate_content(prompt)
            response_text = response.text
            
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_text = response_text[json_start:json_end]
                parsed_data = json.loads(json_text)
                
                entities = []
                for entity_data in parsed_data.get('entities', []):
                    # Extract all fields including any additional properties
                    properties = entity_data.get('properties', {})
                    
                    # If properties are missing, try to extract them from top-level keys
                    for key, value in entity_data.items():
                        if key not in ['name', 'entity_type', 'description', 'properties'] and value is not None:
                            properties[key] = value
                    
                    # Extract code sample from the original content
                    line_number = properties.get('line_number', 0)
                    code_length = properties.get('code_length', 20)
                    if line_number > 0:
                        lines = content.split('\n')
                        start_line = max(0, line_number - 3)
                        end_line = min(len(lines), line_number + code_length + 3)
                        code_sample = '\n'.join(lines[start_line:end_line])
                        properties['context_sample'] = code_sample
                    
                    entities.append(CodeEntity(
                        name=entity_data.get('name'),
                        entity_type=entity_data.get('entity_type'),
                        file_path=file_path,
                        description=entity_data.get('description', ''),
                        properties=properties
                    ))

                relationships = []
                for rel_data in parsed_data.get('relationships', []):
                    relationships.append(CodeRelationship(
                        source=rel_data.get('source'),
                        target=rel_data.get('target'),
                        relationship_type=rel_data.get('relationship_type'),
                        context=rel_data.get('context', '')
                    ))
                
                return entities, relationships
        except Exception as e:
            logger.error(f"AI extraction failed for {file_path}: {e}")
            logger.error(f"Error details: {type(e).__name__}: {str(e)}")
        return [], []

    def extract_with_regex(self, content: str, language: str, file_path: str) -> Tuple[List[CodeEntity], List[CodeRelationship]]:
        """Enhanced fallback regex-based extraction with more patterns and relationship detection."""
        entities = []
        relationships = []
        
        # Enhanced patterns dictionary with more languages and entity types
        patterns = {
            'cobol': {
                'paragraph': r'^[ ]*([A-Z0-9][A-Z0-9-]*)\s*\.',
                'variable': r'^\s*\d+\s+([A-Z0-9-]+)(?:\s+PIC|\s+PICTURE)',
                'file': r'SELECT\s+([A-Z0-9-]+)\s+ASSIGN\s+TO',
                'program': r'PROGRAM-ID.\s+([A-Z0-9-]+)',
                'business_rule': r'^\s*IF\s+(.+?)\s+THEN'
            },
            'c': {
                'function': r'(?:^|\s)(?:static\s+)?(?:void|int|char|float|double|long|size_t|struct\s+\w+|\w+_t)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^;]*\)\s*\{',
                'variable': r'(?:^|\s)(?:static\s+)?(?:int|char|float|double|long|size_t|\w+_t)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|;|\[)',
                'struct': r'struct\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\{',
                'include': r'#include\s*[<"]([^>"]+)[>"]',
                'define': r'#define\s+([a-zA-Z_][a-zA-Z0-9_]*)'
            },
            'cpp': {
                'function': r'(?:^|\s)(?:static\s+)?(?:void|int|char|float|double|long|size_t|bool|auto|std::\w+|struct\s+\w+|\w+::\w+|\w+_t)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^;]*\)\s*(?:const\s*)?\{',
                'method': r'(?:^|\s)(?:virtual\s+)?(?:void|int|char|float|double|long|size_t|bool|auto|std::\w+|\w+::\w+|\w+_t)\s+([a-zA-Z_][a-zA-Z0-9_<>]*)::\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^;]*\)\s*(?:const\s*)?\{',
                'class': r'class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?::|final|\{)',
                'variable': r'(?:^|\s)(?:static\s+)?(?:int|char|float|double|long|size_t|bool|auto|std::\w+|\w+::\w+|\w+_t)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|;|\[)',
                'struct': r'struct\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\{',
                'include': r'#include\s*[<"]([^>"]+)[>"]',
                'define': r'#define\s+([a-zA-Z_][a-zA-Z0-9_]*)'
            },
            'python': {
                'function': r'def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
                'class': r'class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\(|:)',
                'method': r'def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*self',
                'variable': r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?!.*def\s)',
                'import': r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*)(?:\s+as\s+[a-zA-Z_][a-zA-Z0-9_]*)?$',
                'from_import': r'from\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s+import'
            },
            'java': {
                'function': r'(?:public|private|protected|static|\s)+[\w\<\>\[\],\s]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^\)]*\)\s*(?:throws\s+[\w\s,]+)?\s*\{',
                'class': r'(?:public|private|protected)\s+class\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'interface': r'(?:public|private|protected)\s+interface\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'variable': r'(?:public|private|protected|static|\s)+(?:final\s+)?[\w\<\>\[\],\s]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|;)',
                'import': r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*)(?:\s*\*)?;'
            },
            'javascript': {
                'function': r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
                'class': r'class\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'method': r'(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{',
                'arrow_function': r'(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>',
                'variable': r'(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=',
                'import': r'import\s+(?:{[^}]*}|[^{;]*)\s+from\s+[\'"]([^\'"]+)[\'"]'
            },
            'typescript': {
                'function': r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\<?\s*[^>]*\>?\s*\(',
                'class': r'class\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'interface': r'interface\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'method': r'(?:async\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\<?\s*[^>]*\>?\s*\([^)]*\)\s*\{',
                'arrow_function': r'(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>',
                'variable': r'(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:?',
                'type': r'type\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=',
                'import': r'import\s+(?:{[^}]*}|[^{;]*)\s+from\s+[\'"]([^\'"]+)[\'"]'
            },
            'unknown': {
                'function': r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'class': r'class\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                'variable': r'(?:var|let|const)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
            }
        }
        
        # Get patterns for the detected language or use generic ones
        lang_patterns = patterns.get(language, patterns['unknown'])
        
        # Extract filename for unique entity naming
        filename = os.path.basename(file_path)
        filename_base = os.path.splitext(filename)[0]
        
        # Create a file entity first
        file_entity = CodeEntity(
            name=filename,
            entity_type='source_file',
            file_path=file_path,
            description=f"Source file {file_path}",
            properties={
                "path": file_path,
                "language": language,
                "line_count": content.count('\n') + 1
            }
        )
        entities.append(file_entity)
        
        # No special handling for header files to avoid confusion
        
        # Extract entities based on patterns
        for entity_type, pattern in lang_patterns.items():
            for match in re.finditer(pattern, content, re.MULTILINE if language == 'cobol' else re.IGNORECASE | re.MULTILINE):
                try:
                    name = match.group(1)
                except IndexError:
                    # Some patterns might not have capture groups
                    continue
                
                # Get the code snippet around this entity for better context
                line_start = content[:match.start()].count('\n') + 1
                line_end = line_start + content[match.start():match.end()].count('\n') + 1
                
                # Skip if name is too short or starts with underscore (often internal/private)
                if len(name) <= 1 and not name.isalnum():
                    continue
                
                # Create a unique name by appending the filename
                unique_name = f"{name}-{filename_base}"
                
                # Special handling for includes in C/C++
                if entity_type in ('include', 'system_include', 'project_include', 'import', 'from_import'):
                    # Create a simpler include entity type, without distinguishing between system/project headers
                    is_standard_library = False
                    
                    # Try to detect if this is a standard library import
                    if language in ('c', 'cpp'):
                        is_standard_library = '<' in match.group(0) and '>' in match.group(0)
                    elif language == 'python':
                        standard_libs = ['os', 'sys', 're', 'math', 'json', 'time', 'datetime', 'random', 
                                         'collections', 'itertools', 'functools', 'threading', 'multiprocessing']
                        is_standard_library = name.split('.')[0] in standard_libs
                    
                    include_entity = CodeEntity(
                        name=name,  # Keep original name for imports
                        entity_type='import',
                        file_path=file_path,
                        description=f"Import {name} in {filename}",
                        properties={
                            "line_number": line_start,
                            "include_syntax": match.group(0).strip(),
                            "is_standard_library": is_standard_library,
                            "source_file": filename
                        }
                    )
                    entities.append(include_entity)
                    
                    # Add import relationship between file and import
                    relationships.append(CodeRelationship(
                        source=filename,  # Use filename directly
                        target=name,      # Use original import name
                        relationship_type='imports',
                        context=f"{filename} imports {name}"
                    ))
                    continue
                    
                # Get surrounding code for description
                start_pos = max(0, match.start() - 100)
                end_pos = min(len(content), match.end() + 200)
                context_code = content[start_pos:end_pos]
                
                # Add entity with enhanced metadata
                entity = CodeEntity(
                    name=unique_name,  # Use the unique name with filename
                    entity_type=entity_type,
                    file_path=file_path,
                    description=f"{entity_type.capitalize()} '{name}' in {filename}",
                    properties={
                        "original_name": name,  # Store the original name for reference
                        "line_number": line_start,
                        "code_length": line_end - line_start,
                        "context_sample": context_code[:500] if len(context_code) > 500 else context_code,
                        "source_file": filename
                    }
                )
                entities.append(entity)
                
                # Add relationship to file
                relationships.append(CodeRelationship(
                    source=filename,
                    target=unique_name,
                    relationship_type='defines',
                    context=f"File {filename} defines {entity_type} {name}"
                ))
        
        # Enhanced relationship detection - more sophisticated, but now with unique names
        if len(entities) > 1:
            # Process each entity to find potential relationships
            for i, entity in enumerate(entities):
                # Skip the file entity and import entities for relationship detection
                if entity.entity_type in ('source_file', 'import'):
                    continue
                    
                if entity.entity_type in ('function', 'method', 'paragraph'):
                    # Find potential function calls - need to match on original names
                    original_name = entity.properties.get('original_name', entity.name)
                    
                    for j, other_entity in enumerate(entities):
                        if i != j and other_entity.entity_type in ('function', 'method', 'paragraph'):
                            other_original_name = other_entity.properties.get('original_name', other_entity.name)
                            
                            # Only consider entities in the same file to prevent cross-file confusion
                            if entity.properties.get('source_file') != other_entity.properties.get('source_file'):
                                continue
                                
                            # Check if this function's name appears in the content
                            # Add word boundary to prevent partial matches
                            pattern = r'\b' + re.escape(other_original_name) + r'\s*\('
                            if re.search(pattern, content, re.MULTILINE):
                                relationships.append(CodeRelationship(
                                    source=entity.name,
                                    target=other_entity.name,
                                    relationship_type='calls',
                                    context=f"Function {original_name} calls {other_original_name} in {filename}"
                                ))
                    
                    # Detect variable usage within functions
                    for j, other_entity in enumerate(entities):
                        if i != j and other_entity.entity_type in ('variable', 'constant'):
                            # Only consider entities in the same file
                            if entity.properties.get('source_file') != other_entity.properties.get('source_file'):
                                continue
                                
                            other_original_name = other_entity.properties.get('original_name', other_entity.name)
                            
                            # Find start and end of function body
                            function_pattern = r'(?:function|def|void|int|string|bool)\s+' + re.escape(original_name) + r'\s*\([^{]*\)\s*\{(.*?)\}'
                            function_match = re.search(function_pattern, content, re.DOTALL | re.MULTILINE)
                            
                            if function_match:
                                function_body = function_match.group(1)
                                # Check if variable is used in function body
                                var_pattern = r'\b' + re.escape(other_original_name) + r'\b'
                                if re.search(var_pattern, function_body, re.MULTILINE):
                                    relationships.append(CodeRelationship(
                                        source=entity.name,
                                        target=other_entity.name,
                                        relationship_type='uses',
                                        context=f"Function {original_name} uses variable {other_original_name} in {filename}"
                                    ))
                
                # Check for class inheritance - only within the same file
                if entity.entity_type == 'class':
                    original_name = entity.properties.get('original_name', entity.name)
                    
                    # Different patterns for different languages
                    inheritance_patterns = {
                        'python': r'class\s+' + re.escape(original_name) + r'\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)',
                        'java': r'class\s+' + re.escape(original_name) + r'\s+extends\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                        'javascript': r'class\s+' + re.escape(original_name) + r'\s+extends\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                        'php': r'class\s+' + re.escape(original_name) + r'\s+extends\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                        'cpp': r'class\s+' + re.escape(original_name) + r'\s*:\s*(?:public|protected|private)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
                    }
                    
                    pattern = inheritance_patterns.get(language)
                    if pattern:
                        inherit_match = re.search(pattern, content, re.MULTILINE)
                        if inherit_match:
                            parent_class_name = inherit_match.group(1)
                            # Look for parent class in the same file
                            parent_entity = None
                            for e in entities:
                                if (e.entity_type == 'class' and 
                                    e.properties.get('original_name') == parent_class_name and
                                    e.properties.get('source_file') == entity.properties.get('source_file')):
                                    parent_entity = e
                                    break
                                    
                            if parent_entity:
                                relationships.append(CodeRelationship(
                                    source=entity.name,
                                    target=parent_entity.name,
                                    relationship_type='inherits',
                                    context=f"Class {original_name} inherits from {parent_entity.properties.get('original_name')} in {filename}"
                                ))
        
        return entities, relationships

# --- Cloud Function Entry Point ---

# Initialize clients and parser globally to be reused across warm invocations
storage_client = storage.Client()
parser = CodeParser()
PARSED_DATA_BUCKET = os.environ.get('PARSED_DATA_BUCKET')

@functions_framework.cloud_event
def code_parser_entrypoint(cloud_event):
    """GCS-triggered Cloud Function to parse a single code file."""
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    logger.info(f"Processing {file_name} from {bucket_name}.")

    try:
        # Download file content
        source_bucket = storage_client.bucket(bucket_name)
        blob = source_bucket.blob(file_name)
        if not blob.exists():
            logger.error(f"File {file_name} does not exist.")
            return

        # Get metadata to extract repo_id and file information
        metadata = blob.metadata or {}
        repo_id_from_metadata = metadata.get('repo_id')
        file_path_from_metadata = metadata.get('file_path')
        
        logger.info(f"File metadata: {metadata}")
        
        raw_content = blob.download_as_bytes()
        content = ""
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                content = raw_content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            content = raw_content.decode('utf-8', errors='replace')

        # Parse the code
        entities, relationships, context_sample = parser.parse_content(file_name, content)
        
        # Prepare data for upload with improved repo_id extraction
        repo_id = None
        if repo_id_from_metadata:
            repo_id = repo_id_from_metadata
            logger.info(f"Using repo_id from metadata: {repo_id}")
        else:
            # Extract repo_id from path if format is cloned_repos/REPO_ID/...
            repo_id = file_name.split('/')[1] if file_name.startswith('cloned_repos/') and len(file_name.split('/')) > 2 else 'unknown_repo'
            logger.info(f"Extracted repo_id from path: {repo_id}")
        
        output_data = {
            "repo_id": repo_id,
            "filename": file_name,
            "original_path": file_path_from_metadata or file_name,
            "entities": [e.to_dict() for e in entities],
            "relationships": [r.to_dict() for r in relationships],
            "context_sample": context_sample
        }
        
        # Upload results to the parsed data bucket
        if not PARSED_DATA_BUCKET:
            raise ValueError("PARSED_DATA_BUCKET environment variable not set.")
        
        destination_bucket = storage_client.bucket(PARSED_DATA_BUCKET)
        # Create a unique name for the JSON output file
        destination_blob_name = f'parsed_data/{repo_id}/{os.path.basename(file_name)}.json'
        destination_blob = destination_bucket.blob(destination_blob_name)
        
        # Set metadata on the destination blob to help with identification
        destination_blob.metadata = {
            "repo_id": repo_id,
            "original_file": file_name,
            "file_path": file_path_from_metadata or file_name
        }
        
        destination_blob.upload_from_string(
            json.dumps(output_data, indent=2),
            content_type='application/json'
        )
        
        logger.info(f"Successfully parsed {file_name} and uploaded results to {destination_blob_name} with metadata: {destination_blob.metadata}.")

    except Exception as e:
        logger.error(f"Failed to process {file_name}: {e}", exc_info=True)
        raise