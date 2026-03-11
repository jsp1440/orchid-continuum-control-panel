from fastapi import APIRouter

router = APIRouter(prefix="/atlas", tags=["Orchid Atlas"])


@router.get("/")
def atlas_home():
    return {
        "atlas": "Orchid Atlas API",
        "status": "active"
    }


@router.get("/species/{name}")
def atlas_species(name: str):
    return {
        "species": name,
        "message": "Atlas lookup placeholder"
    }
