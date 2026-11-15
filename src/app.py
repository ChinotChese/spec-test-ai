from agents import requirement_extractor, srs_generator
from exporters import markdown
from services import document_loader, evidence_validator, requirement_registry, srs_validator


def generate_srs_pipeline(files):
    # 1. Read and store source passages
    passages = document_loader.ingest(files)
    
    # 2. Extract candidate requirements
    candidates = requirement_extractor.extract(passages)
    
    # 3. Validate evidence and save as pending
    valid_candidates = evidence_validator.validate(candidates)
    requirement_registry.save_candidates(valid_candidates)
    
    # 4. Human review happens outside this function
    
    return valid_candidates


def generate_approved_srs():
    # 5. Only use approved requirements
    requirements = requirement_registry.get_approved()
    
    # 6. Compose SRS using schema and template
    srs = srs_generator.generate(
        requirements=requirements,
        template=srs_template
    )
    
    # 7. Validate and export
    srs_validator.validate(srs, requirements)
    
    return markdown.export(srs, requirements)