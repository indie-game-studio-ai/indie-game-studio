> Fill in the project's working language (studio.json → language); headings may be translated.

# Architecture

Owned by architect.

## Modules
| Module | Path | Responsible for |
|---|---|---|

## Data flow
Input → logic → state → rendering and sound. Who calls whom.

## Logic separate from the engine
Where pure logic lives and what its public API is. qa's acceptance tests target it.

## Determinism
Where the seed comes from, what is forbidden (`Math.random`, wall-clock time in logic).

## Conventions
Naming, folder structure, how to add a scene, an entity, an asset.
