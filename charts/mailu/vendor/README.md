# Vendored upstream chart

`mailu/` is the upstream Helm chart `mailu` 2.6.3 of the Mailu project, with its
subcharts `redis`, `postgresql` and `mariadb` by Bitnami. The chart `mailu` of this
repository includes it with `repository: file://vendor/mailu` under the alias `upstream`,
so its values stay below `upstream:`.

| Part | Source | License |
|---|---|---|
| `mailu/` | `mailu-2.6.3.tgz` from <https://mailu.github.io/helm-charts>, source <https://github.com/Mailu/helm-charts> | The repository names no license; the chart is kept with its own `Chart.yaml`, `README.md` and `CHANGELOG.md` |
| `mailu/charts/redis`, `postgresql`, `mariadb` | Bitnami, packaged in `mailu-2.6.3.tgz` | Apache-2.0, see the header of each template |

## Changes to the upstream files

The chart renders nothing when its value `domain` is empty. Helm switches a dependency
only on a boolean, and a caller that passes only text cannot send one.

- Every template of `mailu/templates` that renders output, `NOTES.txt` included, is
  wrapped in `if .Values.domain`.
- Every template of `mailu/charts/*/templates` that renders output is wrapped in
  `if .mailuParent`, so the subchart renders nothing by itself.
  `mailu/templates/subcharts.yaml` is new: with a domain it renders those templates in
  the context of their subchart (`.Subcharts.<name>`), with `.mailuParent` set.
- The guards add no character to the output, so the checksums that one template takes of
  another stay the same.

No other line is changed. With a domain the chart renders the same objects as the upstream
package.

## Update to a new upstream version

1. Download the new package: `helm pull mailu --repo https://mailu.github.io/helm-charts --version <v>`.
2. Replace `mailu/` with its content and apply the changes above to every template that
   renders output; list the templates of each subchart in `mailu/templates/subcharts.yaml`.
3. Raise `version` of the dependency in `../Chart.yaml` and run `helm dependency update`.
4. Render with and without a domain and compare with the upstream package.
