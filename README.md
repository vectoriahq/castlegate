# Castlegate

Per-person access control for QGIS Server. An administrator decides who may see and edit which layers, features and fields; the server checks each person's OpenID Connect sign-in and answers QGIS Server's access-control questions from those rules.

The design and its decisions are in Groundwork's plan, `docs/plans/qgis-server-access-control.md` in `vectoriahq/groundwork`. This repository is private until licensing advice has been taken; the plan records the intended licence, GPL version 2 or later.

## What is here

- `spike/`: the package 1 prototype and the harness that runs its checks against the Docker QGIS Server. The server plugin itself is package 2 and is not started.
