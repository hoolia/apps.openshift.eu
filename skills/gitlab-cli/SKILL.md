---
name: gitlab-cli
description: Use when working with GitLab merge requests, pipelines or the GitLab API through glab - opening or updating a merge request, reading its review notes and discussions, following a pipeline, or calling an API endpoint.
---

# GitLab CLI

`glab` talks to the host in `GITLAB_HOST` (or the remote of the current repository) with
the token in `GITLAB_TOKEN`.

## Merge requests

- Check the state first: `glab api projects/<path-encoded>/merge_requests/<iid> | jq -r .state`.
  A merged merge request does not take a push: pushing to its branch only creates a new
  branch.
- Open a new one as a draft: `glab mr create --draft --title "<KEY>: <subject>" --description "<body>"`.
  `glab mr update --title` drops the `Draft:` prefix; set `--draft` again or update the
  title through the API.
- Read what reviewers said: `glab api projects/<p>/merge_requests/<iid>/discussions`.
  Unresolved threads have a note with `resolvable: true` and `resolved: false`.
- Answer in the thread: `POST .../discussions/<id>/notes` with `body`; resolve it with
  `PUT .../discussions/<id>?resolved=true` once the commit that fixes it is pushed.
- Assign a reviewer with the query form: `glab api -X PUT "projects/<p>/merge_requests/<iid>?reviewer_ids[]=<user id>"`.

## Pipelines

- `glab ci list --branch <branch>`, `glab ci view <id>`, `glab ci trace <job id>`.
- Commit statuses of a head: `glab api projects/<p>/repository/commits/<sha>/statuses`.

## Pushing

- Pull before you push (`git pull --no-rebase`), never force-push a shared branch.
- One commit, one concern; the subject starts with the issue key when there is one.
