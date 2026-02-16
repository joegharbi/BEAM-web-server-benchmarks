# Unified Benchmarks: Framework Assessment and Design

This document assesses the benchmark framework, proposes a clear naming scheme (so graphs are self-explanatory), and outlines how to support **three base-image variants** for comparable runs.

---

## 1. What the framework looks like today

### Strengths

- **Discovery**: Containers are auto-discovered from `benchmarks/<type>/<language>/<framework>/<container-dir>/`; the directory name = Docker image name = CSV/graph label.
- **Single port**: Host 8001, container 80; one container at a time.
- **Config parity**: num_acceptors, max_connections, base image, etc. in [CONFIGURATION_PARITY.md](CONFIGURATION_PARITY.md).
- **Results**: CSV has "Container Name" = image name; GUI uses it as the series label. So **whatever we use as the directory/image name is what appears on the graph**.

### Unified naming (implemented)

| Container name           | Language | Framework | Version | Base   |
|--------------------------|----------|-----------|---------|--------|
| st-erlang-cowboy-27      | Erlang   | Cowboy    | 27      | bookworm-slim  |
| st-erlang-pure-23/26/27  | Erlang   | pure      | 23/26/27| bookworm-slim  |
| st-erlang-index-23/26/27 | Erlang   | index     | 23/26/27| bookworm-slim  |
| st-erlang-yaws-26/27     | Erlang   | Yaws      | 26/27   | bookworm-slim  |
| st-elixir-cowboy-1-16    | Elixir   | Cowboy    | 1.16    | bookworm-slim  |
| st-elixir-pure-1-16      | Elixir   | pure      | 1.16    | bookworm-slim  |
| st-elixir-phoenix-1-8    | Elixir   | Phoenix   | 1.8     | bookworm-slim  |
| st-gleam-mist-1-0        | Gleam    | Mist      | 1.0     | Alpine         |

Names follow `<type>-<language>-<framework>-<version>` so graphs are self-explanatory.

---

## 2. Proposed naming scheme (graph-friendly and encoding base)

Goal: **From the container name alone, we know: type, language, framework, version, and base image.**

### Format

```
<type>-<language>-<framework>-<version>[-<base>]
```

- **type**: `st` (static), `dy` (dynamic), `ws` (websocket)
- **language**: `erlang`, `elixir`, `gleam`
- **framework**: `cowboy`, `phoenix`, `pure`, `index`, `yaws`, `mist`
- **version**: OTP/Elixir/Phoenix/Gleam version (e.g. `27`, `1-16`, `1-8`, `1-0`)
- **base** (optional): `bw` = bookworm-slim, `full` = full Elixir image, `alpine` = Alpine (Gleam)

When there is only one base for a stack, we can omit the suffix and document it (e.g. all Erlang = bw).

### Examples (after rename / new containers)

| New name                         | Meaning                                      | Base        |
|----------------------------------|----------------------------------------------|-------------|
| st-erlang-cowboy-27              | Static, Erlang, Cowboy, OTP 27               | bookworm-slim |
| dy-erlang-pure-27                | Dynamic, Erlang, pure, OTP 27                 | bookworm-slim |
| st-erlang-index-27               | Static, Erlang, index (serve file)           | bookworm-slim |
| st-elixir-cowboy-1-16-bw        | Static, Elixir, Cowboy, 1.16, bookworm-slim  | bookworm-slim |
| st-elixir-cowboy-1-16-full      | Static, Elixir, Cowboy, 1.16, full Elixir    | elixir:1.16 |
| st-elixir-pure-1-16-bw          | Static, Elixir, pure, 1.16, bookworm-slim    | bookworm-slim |
| st-gleam-mist-1-0-alpine        | Static, Gleam, Mist, 1.0                     | Alpine      |
| ws-erlang-cowboy-27             | WebSocket, Erlang, Cowboy, 27                | bookworm-slim |
| ws-elixir-phoenix-1-8-bw       | WebSocket, Elixir, Phoenix, 1.8, bookworm-slim | bookworm-slim |

So on the graph:

- **st-erlang-cowboy-27** → clearly Erlang Cowboy OTP 27 (and we document “Erlang = bw”).
- **st-elixir-cowboy-1-16-bw** vs **st-elixir-cowboy-1-16-full** → same stack, two bases; easy to compare.

### What to change

1. **Rename directories** (and thus image names) to the new scheme. Scripts use `basename` of the dir containing the Dockerfile, so renaming is enough for discovery and CSV/graph labels.
2. **Add full-Elixir variants** for a subset of Elixir containers (e.g. cowboy, phoenix, pure, index) so we have both:
   - `-bw`: multi-stage, runtime = debian:bookworm-slim
   - `-full`: single-stage, runtime = elixir:1.16

---

## 3. Three base images (unified benchmarks)

You want **three** base-image configurations across the suite:

| Base              | Description                    | Current use                          | Naming suffix |
|-------------------|--------------------------------|--------------------------------------|---------------|
| **bookworm-slim** | Minimal Debian runtime         | Erlang, Elixir (multi-stage)         | `-bw` or omit for Erlang |
| **elixir:1.16**   | Full Elixir image as runtime   | Not used today (we switched to bw)   | `-full`       |
| **Alpine**        | Gleam official image           | Gleam only                           | `-alpine` or omit for Gleam |

To get “all three bases with all different configurations”:

1. **Erlang**  
   - Keep as-is: all use bookworm-slim.  
   - Optionally rename to the new scheme (e.g. st-erlang-cowboy-27) and document “Erlang = bw”.

2. **Elixir**  
   - **Current**: All Elixir containers use multi-stage → bookworm-slim.  
   - **Add**: For each important Elixir container (cowboy, phoenix, pure, index – static and dynamic, and websocket if desired), add a **second** image that uses `FROM elixir:1.16` (no multi-stage) and the same app code.  
   - Name the new ones with `-full`, e.g. `st-elixir-cowboy-1-16-full`.  
   - Name the existing ones with `-bw`, e.g. `st-elixir-cowboy-1-16-bw`, so the graph clearly shows base.

3. **Gleam**  
   - Keep Alpine; optionally rename to e.g. st-gleam-mist-1-0-alpine and document “Gleam = Alpine”.

Result: when you plot, you see:

- Erlang (bw)
- Elixir (bw) vs Elixir (full)
- Gleam (alpine)

and can compare “same stack, different base” (e.g. st-elixir-cowboy-1-16-bw vs st-elixir-cowboy-1-16-full).

---

## 4. Missing parts and suggested updates

### 4.1 Naming

- [ ] **Adopt the naming scheme** above and rename container directories (and update BENCHMARKS_AUDIT.md, README, any docs that list image names).
- [ ] **Decide default suffix**: e.g. Erlang/Gleam omit base (document once); only Elixir uses `-bw` and `-full` so the graph is clear.

### 4.2 Base-image variants

- [ ] **Add full-Elixir variants**: For chosen Elixir containers, add a second Dockerfile (or a second dir) that uses `FROM elixir:1.16` and same app; name with `-full`. Keep existing multi-stage images and name with `-bw`.

### 4.3 Discovery and scripts

- [ ] Scripts already use directory name = image name; no code change needed for discovery if we only rename dirs and add new dirs for `-full` / `-alpine`.
- [ ] **Health and run**: Same `make build`, `make check-health`, `make run`; new images are discovered automatically.

### 4.4 Graph labels

- [ ] Graph label = "Container Name" from CSV = image name. So the **new names are the graph labels**. Optional later: add a small metadata file per container (e.g. `display_label`, `base_image`) and have the measurement script pass them as extra CSV columns so the GUI could show a longer "Display Label" if desired.

### 4.5 Documentation

- [ ] **BENCHMARKS_AUDIT.md**: Update table with new names and a column “Base image”.
- [ ] **CONFIGURATION_PARITY.md** or **FAIRNESS_ASSESSMENT.md**: State the three bases and that naming encodes them (bw / full / alpine).
- [ ] **README**: Short “Naming” subsection: type-language-framework-version[-base]; list base suffixes (bw, full, alpine).

### 4.6 Optional: metadata file for display

- [ ] Optional: Add `benchmarks/.../container-name/metadata.json` with e.g. `{"base": "bookworm-slim", "display_label": "Erlang Cowboy 27 (bw)"}`. Measurement scripts could read it and add a "Display Label" or "Base Image" column to the CSV so the GUI can use a friendlier label without changing the image name.

---

## 5. Suggested order of work

1. **Define final naming** (agree on scheme and whether to use `-bw` for all bookworm-slim or only for Elixir).
2. **Rename existing containers** to the new scheme (and update docs). This is a one-time, mechanical rename.
3. **Add full-Elixir variants** for the Elixir containers you care about (e.g. st/dy-elixir-cowboy-1-16-full, st/dy-elixir-phoenix-1-8-full, st/dy-elixir-pure-1-16-full, st/dy-elixir-index-1-16-full, and optionally ws-*).
4. **Update BENCHMARKS_AUDIT.md** (and any other docs) with the new names and base-image column.
5. **Re-run benchmarks** and regenerate graphs; legend will show the new, self-explanatory names (including base where it matters).

---

## 6. Summary

- **What we have**: Single port, config parity, auto-discovery, CSV/graph by container name.
- **What we need for “unified” and clear graphs**: (1) a **consistent naming scheme** that encodes type, language, framework, version, and base; (2) **three base variants** implemented as: Erlang/Gleam as today (bw / alpine), Elixir split into **-bw** (bookworm-slim) and **-full** (elixir:1.16).
- **Missing pieces**: Rename to the new scheme, add full-Elixir image variants, update docs. Optionally add metadata for richer graph labels later.
