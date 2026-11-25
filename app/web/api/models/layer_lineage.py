from pydantic import BaseModel


class LineageLayerSchema(BaseModel):
    schemaName: str
    tables: int


class LineageLayer(BaseModel):
    layer: str
    connectionId: str | None
    sourceName: str | None
    engine: str | None
    database: str | None
    schemas: list[LineageLayerSchema]
    objectCount: int


class LineageLayerDependency(BaseModel):
    sourceLayer: str
    targetLayer: str


class LayerLineage(BaseModel):
    layers: list[LineageLayer]
    dependencies: list[LineageLayerDependency]
