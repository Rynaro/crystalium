"""GraphML and Cytoscape adapter — pure functions of the canonical graph JSON (D2, §9).

These adapters are MECHANICAL transforms of the canonical dict produced by
GraphExporter.export(). They NEVER re-derive edges or re-read the store (§9.3).
Both are count-preserving (G-GE8): m <node>/node-objects + n <edge>/edge-objects.

Spec §9:
  §9.1  to_graphml(canonical)  -> str  — GraphML XML document
  §9.2  to_cytoscape(canonical) -> dict — Cytoscape.js elements JSON

GraphML term collision (documented per §9.1):
  - GraphML <edge source="..."> means an endpoint id, NOT the CRYSTALIUM
    `source` tag (kuzu/derived). The CRYSTALIUM `source` tag is emitted as
    `<data key="source">` inside the edge element.

Cytoscape term collision (documented per §9.2):
  - Cytoscape data.source / data.target are reserved for the endpoint node ids.
    The CRYSTALIUM `source` tag (kuzu/derived) is remapped to `edge_source`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

# ---------------------------------------------------------------------------
# GraphML adapter (§9.1)
# ---------------------------------------------------------------------------

_GRAPHML_NS = "http://graphml.graphdrawing.org/graphml"
_GRAPHML_XSD = (
    "http://graphml.graphdrawing.org/graphml "
    "http://graphml.graphdrawing.org/graphml/1.0/graphml.xsd"
)

# Node attribute keys (<key for="node">)
_NODE_KEYS: list[tuple[str, str, str]] = [
    # (attr_name, key_id, type_str)
    ("layer",             "layer",            "string"),
    ("summary",           "summary",          "string"),
    ("trust_tier",        "trust_tier",       "string"),
    ("validation_state",  "validation_state", "string"),
    ("status",            "status",           "string"),
    ("importance",        "importance",       "double"),
]

# Edge attribute keys (<key for="edge">)
_EDGE_KEYS: list[tuple[str, str, str]] = [
    ("type",   "type",   "string"),
    ("source", "source", "string"),   # CRYSTALIUM source tag (kuzu/derived)
    ("weight", "weight", "double"),
]


def to_graphml(canonical: dict[str, Any]) -> str:
    """Transform a canonical graph-export dict to a GraphML XML document (§9.1).

    Count-preserving: emits exactly len(canonical['nodes']) <node> elements
    and len(canonical['edges']) <edge> elements (G-GE8).

    The CRYSTALIUM `source` tag (kuzu/derived) is emitted as
    `<data key="source">` inside each edge element — distinct from the GraphML
    attribute `source=` on the <edge> tag, which holds the endpoint node id.

    Returns:
        A UTF-8 XML string (no XML declaration; root is <graphml>).
    """
    nodes: list[dict[str, Any]] = canonical.get("nodes", [])
    edges: list[dict[str, Any]] = canonical.get("edges", [])

    # ---- Root <graphml> --------------------------------------------------
    root = ET.Element(
        "graphml",
        attrib={
            "xmlns": _GRAPHML_NS,
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": _GRAPHML_XSD,
        },
    )

    # ---- <key> declarations for node attrs --------------------------------
    for _attr_name, key_id, type_str in _NODE_KEYS:
        ET.SubElement(
            root, "key",
            attrib={"id": key_id, "for": "node", "attr.name": key_id, "attr.type": type_str},
        )

    # ---- <key> declarations for edge attrs --------------------------------
    for _attr_name, key_id, type_str in _EDGE_KEYS:
        ET.SubElement(
            root, "key",
            attrib={"id": key_id, "for": "edge", "attr.name": key_id, "attr.type": type_str},
        )

    # ---- <graph> --------------------------------------------------------
    graph_el = ET.SubElement(
        root, "graph",
        attrib={"id": "G", "edgedefault": "directed"},
    )

    # ---- <node> elements ------------------------------------------------
    for node in nodes:
        node_el = ET.SubElement(graph_el, "node", attrib={"id": str(node["id"])})
        for attr_name, key_id, _type_str in _NODE_KEYS:
            val = node.get(attr_name)
            if val is not None:
                data_el = ET.SubElement(node_el, "data", attrib={"key": key_id})
                data_el.text = str(val)

    # ---- <edge> elements ------------------------------------------------
    for edge in edges:
        # GraphML source/target = endpoint ids (NOT the CRYSTALIUM source tag)
        edge_el = ET.SubElement(
            graph_el, "edge",
            attrib={
                "source": str(edge["from"]),
                "target": str(edge["to"]),
            },
        )
        # CRYSTALIUM edge attributes as <data> children
        for attr_name, key_id, _type_str in _EDGE_KEYS:
            val = edge.get(attr_name)
            if val is not None:
                data_el = ET.SubElement(edge_el, "data", attrib={"key": key_id})
                data_el.text = str(val)

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


# ---------------------------------------------------------------------------
# Cytoscape adapter (§9.2)
# ---------------------------------------------------------------------------


def to_cytoscape(canonical: dict[str, Any]) -> dict[str, Any]:
    """Transform a canonical graph-export dict to Cytoscape.js elements JSON (§9.2).

    Count-preserving: emits exactly len(canonical['nodes']) node objects
    and len(canonical['edges']) edge objects (G-GE8).

    Per §9.2:
      - Each node:  {"data": {"id": id, "layer": ..., "summary": ...,
                               "trust_tier": ..., "validation_state": ...,
                               "status": ..., "importance": ...}}
      - Each edge:  {"data": {"id": "{type}:{from}->{to}",
                               "source": from,   # Cytoscape endpoint (reserved)
                               "target": to,     # Cytoscape endpoint (reserved)
                               "type": type,
                               "edge_source": source,  # CRYSTALIUM source tag remapped
                               "weight": weight}}

    The CRYSTALIUM `source` tag (kuzu/derived) is remapped to `edge_source`
    because Cytoscape reserves `data.source` and `data.target` for endpoints.

    Returns:
        {"elements": {"nodes": [...], "edges": [...]}}
    """
    nodes: list[dict[str, Any]] = canonical.get("nodes", [])
    edges: list[dict[str, Any]] = canonical.get("edges", [])

    cy_nodes: list[dict[str, Any]] = []
    for node in nodes:
        cy_nodes.append({
            "data": {
                "id":               node["id"],
                "layer":            node.get("layer", ""),
                "summary":          node.get("summary", ""),
                "trust_tier":       node.get("trust_tier", ""),
                "validation_state": node.get("validation_state", ""),
                "status":           node.get("status", ""),
                "importance":       node.get("importance", 0.0),
            }
        })

    cy_edges: list[dict[str, Any]] = []
    for edge in edges:
        from_id = edge["from"]
        to_id   = edge["to"]
        etype   = edge.get("type", "")
        # CRYSTALIUM source tag remapped to edge_source (Cytoscape reserved-word
        # collision documented in §9.2 and spec §9 "Cytoscape term collision").
        cy_edges.append({
            "data": {
                "id":          f"{etype}:{from_id}->{to_id}",
                "source":      from_id,    # Cytoscape endpoint (reserved)
                "target":      to_id,      # Cytoscape endpoint (reserved)
                "type":        etype,
                "edge_source": edge.get("source", ""),   # kuzu | derived
                "weight":      edge.get("weight", 1.0),
            }
        })

    return {"elements": {"nodes": cy_nodes, "edges": cy_edges}}
