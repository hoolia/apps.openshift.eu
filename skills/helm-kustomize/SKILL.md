---
name: helm-kustomize
description: Use when rendering, changing or checking a Helm chart or a Kustomize overlay - adding a value, a template or a resource, pinning a chart version, or proving what a change renders.
---

# Helm and Kustomize

Render first, read the output, then change.

## Render

- A chart: `helm template <release> <chart dir> --namespace <ns> -f <values>`. Pass
  `--api-versions route.openshift.io/v1 --api-versions security.openshift.io/v1` to render
  as OpenShift does; without them some subcharts render a fixed UID the cluster refuses.
- An overlay: `kustomize build --enable-helm <dir>` (or `oc kustomize --enable-helm <dir>`).
  Set `namespace:` on every `helmCharts` entry: the overlay's own `namespace:` does not set
  Helm's release namespace.
- Remove what a render downloads (`charts/` under an overlay) before you commit.

## Change

- A value the chart does not read is silently ignored. After each change, render and grep
  the output for the field you meant to change.
- Pin exact chart and image versions; never `latest`.
- Every default lives in `values.yaml`; a template has no literal that belongs in a value.
- A new file in an overlay goes into its `kustomization.yaml` `resources:` list.
- Render twice and compare when names come from `.Release.Namespace` or random helpers:
  a changing name means a missing input.

## Check

- `helm lint <chart dir>` and a render with the chart's defaults only.
- Diff the render against the live cluster: `kustomize build --enable-helm <dir> | oc diff -f -`.
