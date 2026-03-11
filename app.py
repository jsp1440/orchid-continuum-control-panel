from api.server import app
from api.atlas import router as atlas_router

# attach Orchid Atlas routes
app.include_router(atlas_router)
