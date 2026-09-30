"""MRAC feature descriptor, read offline from the firmware ELF.

The firmware publishes its regressor layout as two flash-resident objects (API/mrac.c):

    const uint8_t            mrac_n_features;               // live feature count N
    const MRAC_FeatureDesc_t mrac_feature_desc[N];          // {index, name, block, group}

`mrac_state.<axis>.Theta[]` / `.Phi[]` are sized by the build's MRAC_CAPACITY, which can
exceed N, so N has to come from `mrac_n_features` and not from the DWARF array length.
Reading both from the ELF keeps every host-side consumer (subscribe presets, the
per-element parameter encoder, plots) tied to the build being analysed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .elf_const import DEFAULT_ELF, ElfConstReader

COUNT_SYMBOL = "mrac_n_features"
DESC_SYMBOL = "mrac_feature_desc"


@dataclass(frozen=True)
class MracFeature:
    """One row of `mrac_feature_desc`. `block` / `group` are the C enumerator names."""
    index: int
    name: str
    block: str
    group: str


def features_from(elf: ElfConstReader) -> list[MracFeature]:
    """Descriptor rows from an open ELF reader; the count is `mrac_n_features`."""
    blocks = elf.resolver.enumerators(f"{DESC_SYMBOL}[0].block")
    groups = elf.resolver.enumerators(f"{DESC_SYMBOL}[0].group")
    out = []
    for i, row in enumerate(elf.counted_array(DESC_SYMBOL, COUNT_SYMBOL)):
        index = int(elf.scalar(f"{row}.index"))
        if index != i:
            raise ValueError(f"{row}.index = {index}, expected {i}: "
                             f"the descriptor must list features in index order")
        block = int(elf.scalar(f"{row}.block"))
        group = int(elf.scalar(f"{row}.group"))
        out.append(MracFeature(
            index=index,
            name=elf.cstring(int(elf.scalar(f"{row}.name"))),
            block=blocks.get(block, f"<{block}>"),
            group=groups.get(group, f"<{group}>"),
        ))
    return out


def read_mrac_features(elf_path: str | Path = DEFAULT_ELF) -> list[MracFeature]:
    """The firmware's MRAC feature descriptor, read offline from `elf_path`."""
    with ElfConstReader(elf_path) as elf:
        return features_from(elf)


def read_mrac_n_features(elf_path: str | Path = DEFAULT_ELF) -> int:
    """`mrac_n_features` from `elf_path` (cheaper than the full descriptor)."""
    with ElfConstReader(elf_path) as elf:
        return int(elf.scalar(COUNT_SYMBOL))


def source_n_features(variant_header: str | Path) -> int:
    """MRAC_N_FEATURES of the default variant as written in API/mrac_variant.h.

    The header defines the count as MRAC_N_STRUCT + MRAC_N_RBF + MRAC_N_SINDY, each set per variant, so this sums
    the three in the branch of the default variant. Tests use it to compare the source with the ELF.
    """
    text = Path(variant_header).read_text(encoding="latin-1")
    default = re.search(r"#\s*define\s+MRAC_VARIANT\s+(MRAC_VARIANT_\w+)", text)
    if not default:
        raise ValueError("default MRAC_VARIANT not found in %s" % variant_header)
    branch = re.search(r"#\s*(?:el)?if\s+MRAC_VARIANT\s*==\s*%s\b(.*?)#\s*(?:elif|else|endif)" % default.group(1),
                       text, re.S)
    if not branch:
        raise ValueError("branch of %s not found in %s" % (default.group(1), variant_header))
    total = 0
    for part in ("STRUCT", "RBF", "SINDY"):
        define = re.search(r"#\s*define\s+MRAC_N_%s\s+(\d+)" % part, branch.group(1))
        if not define:
            raise ValueError("MRAC_N_%s not defined for %s" % (part, default.group(1)))
        total += int(define.group(1))
    return total
