from agents import requirement_extractor, srs_generator
from exporters import markdown
from services import document_loader, evidence_validator, requirement_registry, srs_validator

# Every run is for ONE project (create it with services.project_registry).
# Its documents, requirements and SRS all carry that project_id.


def generate_srs_pipeline(files, project_id):
    # 1. Read and store source passages, linked to the project
    passages = document_loader.ingest(files, project_id=project_id)

    # 2. Extract candidate requirements
    candidates = requirement_extractor.extract(passages)

    # 3. Validate evidence and save as pending, in the project
    valid_candidates = evidence_validator.validate(candidates)
    requirement_registry.save_candidates(valid_candidates, project_id=project_id)

    # 4. Human review happens outside this function

    return valid_candidates


def generate_approved_srs(project_id):
    # 5. Only use the project's approved requirements
    requirements = requirement_registry.get_approved(project_id=project_id)

    # 6. Compose SRS using schema and template
    srs = srs_generator.generate(
        requirements=requirements
    )

    # 7. Validate and export
    srs_validator.validate(srs, requirements, project_id=project_id)

    return markdown.export(srs, requirements, project_id=project_id)
