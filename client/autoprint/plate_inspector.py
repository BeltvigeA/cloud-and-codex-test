from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

try:  # pragma: no cover - optional dependency
    from PIL import Image  # type: ignore[attr-defined]
except Exception as import_error:  # pragma: no cover - import guarded at runtime
    Image = None  # type: ignore[assignment]
    PIL_IMPORT_ERROR = import_error
else:
    PIL_IMPORT_ERROR = None

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import Image  # noqa: F401  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

REFERENCE_PATTERN = re.compile(r"(\d+)")
DEFAULT_HASH_SIZE = 8
DEFAULT_MAX_HAMMING = 30
DEFAULT_MIN_ENTROPY_DELTA = 0.25


def _collectReferenceEntries(referenceDirectory: Path) -> List[Tuple[int, Path]]:
    entries: List[Tuple[int, Path]] = []
    for candidate in sorted(referenceDirectory.glob("*.jpg")):
        match = REFERENCE_PATTERN.search(candidate.stem)
        index = int(match.group(1)) if match else len(entries)
        entries.append((index, candidate))
    return entries


def _resolveReferenceCandidates(entries: Sequence[Tuple[int, Path]], hint: Optional[float]) -> List[Tuple[int, Path]]:
    if not entries:
        return []
    if hint is None:
        return list(entries)
    try:
        hintValue = float(hint)
    except (TypeError, ValueError):
        return list(entries)
    maxIndex = max(index for index, _ in entries)
    if maxIndex <= 0:
        return [entries[0]]
    if 0.0 <= hintValue <= 100.0:
        targetIndex = int(round((hintValue / 100.0) * maxIndex))
    else:
        targetIndex = int(round(hintValue))
    window = 2
    candidates: List[Tuple[int, Path]] = []
    for index, path in entries:
        if abs(index - targetIndex) <= window:
            candidates.append((index, path))
    if candidates:
        return candidates
    nearest = min(entries, key=lambda entry: abs(entry[0] - targetIndex))
    return [nearest]


def _ensurePillowReady() -> bool:
    if Image is not None:
        return True
    log.warning(
        "[inspect] Pillow is not available; skipping brake inspection (error=%s)",
        PIL_IMPORT_ERROR,
    )
    return False


def _prepareImage(image: "Image.Image") -> "Image.Image":
    return image.convert("L")


def _mean(values: Sequence[float]) -> float:
    return sum(values) / float(len(values) or 1)


def _a_hash(image: "Image.Image", size: int = DEFAULT_HASH_SIZE) -> int:
    grayscale = image.convert("L")
    resized = grayscale.resize((size, size))
    pixels = list(resized.getdata())
    average = _mean(pixels)
    result = 0
    for index, pixel in enumerate(pixels):
        if pixel >= average:
            result |= 1 << index
    return result


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _entropy(image: "Image.Image") -> float:
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    total = float(sum(histogram))
    if total <= 0:
        return 0.0
    entropyValue = 0.0
    for count in histogram:
        if count <= 0:
            continue
        probability = count / total
        entropyValue -= probability * math.log2(probability)
    return entropyValue


def compareToReference(
    serial: str,
    snapshotPath: Path,
    *,
    ref_index_hint: Optional[float] = None,
    max_hamming: int = DEFAULT_MAX_HAMMING,
    min_entropy_delta: float = DEFAULT_MIN_ENTROPY_DELTA,
) -> bool:
    if not _ensurePillowReady():
        return False
    sanitizedSerial = str(serial or "").strip()
    if not sanitizedSerial:
        raise ValueError("Serial number is required for inspection")
    snapshotFile = Path(snapshotPath)
    if not snapshotFile.exists():
        log.warning("[inspect] snapshot %s does not exist", snapshotFile)
        return False
    referenceDirectory = Path.home() / ".printmaster" / "bed-reference" / sanitizedSerial
    if not referenceDirectory.exists():
        log.warning("[inspect] reference directory missing for %s", sanitizedSerial)
        return False
    referenceEntries = _collectReferenceEntries(referenceDirectory)
    if not referenceEntries:
        log.warning("[inspect] no reference frames for %s", sanitizedSerial)
        return False
    try:
        with Image.open(snapshotFile) as snapshotImage:  # type: ignore[union-attr]
            preparedSnapshot = _prepareImage(snapshotImage)
            snapshotHash = _a_hash(preparedSnapshot)
            snapshotEntropy = _entropy(preparedSnapshot)
    except Exception as error:
        log.warning("[inspect] unable to open snapshot %s: %s", snapshotFile, error)
        return False
    bestMatch: Optional[Dict[str, object]] = None
    for index, candidate in _resolveReferenceCandidates(referenceEntries, ref_index_hint):
        try:
            with Image.open(candidate) as referenceImage:  # type: ignore[union-attr]
                preparedReference = _prepareImage(referenceImage)
                referenceHash = _a_hash(preparedReference)
                hammingDistance = _hamming(snapshotHash, referenceHash)
                entropyDelta = abs(snapshotEntropy - _entropy(preparedReference))
        except Exception as error:
            log.debug("[inspect] unable to open reference %s: %s", candidate, error)
            continue
        if bestMatch is None or hammingDistance < int(bestMatch["hamming"]):
            bestMatch = {
                "index": index,
                "path": candidate,
                "hamming": hammingDistance,
                "entropyDelta": entropyDelta,
            }
    if bestMatch is None:
        log.warning("[inspect] unable to compare %s against reference", snapshotFile)
        return False
    isClean = bool(bestMatch["hamming"] <= max_hamming and bestMatch["entropyDelta"] <= min_entropy_delta)
    log.info(
        "[inspect] %s best=idx%s hamming=%d entropyΔ=%.3f clean=%s",  # noqa: G003
        sanitizedSerial,
        bestMatch["index"],
        bestMatch["hamming"],
        bestMatch["entropyDelta"],
        isClean,
    )
    return isClean


def compare_to_reference(
    serial: str,
    snapshotPath: Path,
    *,
    ref_index_hint: Optional[float] = None,
    max_hamming: int = DEFAULT_MAX_HAMMING,
    min_entropy_delta: float = DEFAULT_MIN_ENTROPY_DELTA,
) -> bool:
    return compareToReference(
        serial,
        snapshotPath,
        ref_index_hint=ref_index_hint,
        max_hamming=max_hamming,
        min_entropy_delta=min_entropy_delta,
    )

