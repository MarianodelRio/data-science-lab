"""Error type shared across the graph-assembly package."""


class GraphBuilderError(Exception):
    """Raised for genuine graph-assembly bugs: an ambiguous/missing node class
    in a landed module, an unexpected supervisor phase, or any other
    programming error distinct from "node not implemented yet" (which is
    handled by falling back to `NoOpNode`, not by raising).
    """
