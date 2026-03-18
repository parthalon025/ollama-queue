"""Forge archive API endpoints — MAP-Elites grid visualization."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import ollama_queue.api as _api
from ollama_queue.forge.descriptors import DEFAULT_GRID_SIZE

router = APIRouter(tags=["forge"])


@router.get("/api/forge/archive")
def get_forge_archive():
    cells = _api.db.get_forge_archive_grid()
    qd_score = sum(c.get("fitness", 0.0) for c in cells)
    coverage = len(cells) / (DEFAULT_GRID_SIZE**2) if cells else 0.0
    return {
        "cells": cells,
        "qd_score": round(qd_score, 6),
        "coverage": coverage,
        "grid_size": DEFAULT_GRID_SIZE,
    }


@router.get("/api/forge/archive/heatmap")
def get_forge_archive_heatmap(grid_size: int = Query(DEFAULT_GRID_SIZE, ge=1, le=50)):
    cells = _api.db.get_forge_archive_grid()
    heatmap: list[list[float | None]] = [[None] * grid_size for _ in range(grid_size)]
    for c in cells:
        x, y = c["x_bin"], c["y_bin"]
        if 0 <= x < grid_size and 0 <= y < grid_size:
            heatmap[y][x] = c["fitness"]
    return {"heatmap": heatmap, "grid_size": grid_size}


@router.get("/api/forge/archive/cell")
def get_forge_archive_cell(x: int = Query(...), y: int = Query(...)):
    cell = _api.db.get_forge_archive_cell(x, y)
    if cell is None:
        raise HTTPException(404, detail="Archive cell not found")
    return cell
