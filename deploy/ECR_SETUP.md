# ECR setup

Two paths:
- **Path A — local push (no GitHub)**: `aws sso login` → `make ecr-create-repos` → `make ecr-build-push`. Done. Use this for first-time testing.
- **Path B — automated CI push via OIDC**: terraform creates the IAM role + ECR repos, then every push to your fork's `main` re-publishes. Use once you're committing changes regularly.

---

## Path A — local push (recommended first run)

```bash
# One-time
aws sso login                  # whichever profile gets you into the AIES dev account
make ecr-create-repos          # creates private ECR + ECR Public repos

# Every time you want to push a new build
make ecr-build-push            # buildx-pushes both images
```

That's it. No GitHub, no commits, no terraform. Images live in ECR after
about 5-10 min (vLLM image is ~5GB so the worker push dominates).

Override defaults if needed:
```bash
TAG=v0.1.1 make ecr-build-push            # custom tag
AWS_ACCOUNT_ID=12345 make ecr-build-push  # different account
ECR_PUBLIC_ALIAS=myorg make ecr-build-push  # different public ECR alias
```

After the push, `helm install` against your EKS cluster will pull the
gateway from private ECR (via node IAM, no secret) and PI's custom template
points at `public.ecr.aws/aies/serverlessgpu-pi-worker:latest`.

> **First time using ECR Public on this AWS account?** Claim a public alias
> once via the AWS Console (ECR → Public registries → "Get started"). The
> Makefile defaults to `aies` — change `ECR_PUBLIC_ALIAS` if your alias
> differs.

---

## Path B — automated CI push via OIDC (later)

The serverless-gpu CI pushes two images to AWS ECR:
- **Gateway** → private ECR (EKS pulls via node IAM role)
- **PI worker** → ECR Public Gallery (Prime Intellect pulls without AWS auth)

Both pushes use one IAM role (`AIES-Serverlessgpu-ECRAccessRole`), assumed by
GitHub Actions via OIDC. Set this up once, after which every CI run on `main`
publishes both images.

> **Where this gets applied**: the AIES `infrastructure` repo, path
> `terraform/environments/dev/shared/ecr/`. This repo doesn't touch terraform
> directly — instructions only.

---

## Step 1 — add the private ECR repo

Add an entry to `terraform/environments/dev/shared/terraform.tfvars` under
`ecr_definitions`:

```hcl
{
  ecr_names     = ["aies/serverlessgpu-gateway"]
  ecr_role_name = "AIES-Serverlessgpu-ECRAccessRole"
  github_repos  = ["https://github.com/<owner>/Serverless-GPU"]
}
```

Replace `<owner>` with whichever GitHub org/user holds the fork. Multiple
forks can share the same ECR by adding more URLs to `github_repos`.

`terraform apply` creates:
- `865626945255.dkr.ecr.ap-southeast-5.amazonaws.com/aies/serverlessgpu-gateway`
- IAM role `AIES-Serverlessgpu-ECRAccessRole` assumable from the listed repos
- Lifecycle: keep last 30 `v*`-tagged images
- Inline policies: `ecr:GetAuthorizationToken` + repository read/write

This is enough for the **gateway** image. For the **PI worker**, we need ECR
Public, which the existing terraform module doesn't yet create — see step 2.

---

## Step 2 — add ECR Public + IAM extension for the worker

The existing `ecr/` module (`terraform-aws-modules/ecr/aws`) creates private
ECR repos only. ECR Public is a separate AWS API and resource type. Add a
small Terraform addendum (in the same `ecr/` directory or a sibling module):

```hcl
# ECR Public is always us-east-1, regardless of where your other resources live.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "aws_ecrpublic_repository" "serverlessgpu_pi_worker" {
  provider        = aws.us_east_1
  repository_name = "serverlessgpu-pi-worker"

  catalog_data {
    description       = "Serverless-GPU worker — vLLM + worker-agent. Pulled by Prime Intellect."
    architectures     = ["x86-64"]
    operating_systems = ["Linux"]
  }
}

# Extend the existing GitHub Actions role to allow ECR Public push.
# The role itself is created by policies.tf in this directory.
resource "aws_iam_role_policy" "ecr_public_push" {
  name = "AIES-Serverlessgpu-ECRPublic-Push"
  role = aws_iam_role.github_actions["AIES-Serverlessgpu-ECRAccessRole"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRPublicAuth"
        Effect = "Allow"
        Action = [
          "ecr-public:GetAuthorizationToken",
          "sts:GetServiceBearerToken",
        ]
        Resource = "*"
      },
      {
        Sid    = "ECRPublicPush"
        Effect = "Allow"
        Action = [
          "ecr-public:BatchCheckLayerAvailability",
          "ecr-public:CompleteLayerUpload",
          "ecr-public:InitiateLayerUpload",
          "ecr-public:PutImage",
          "ecr-public:UploadLayerPart",
        ]
        Resource = aws_ecrpublic_repository.serverlessgpu_pi_worker.arn
      },
    ]
  })
}
```

`terraform apply` again. Creates:
- `public.ecr.aws/<your-public-alias>/serverlessgpu-pi-worker`
- An inline policy on the existing GitHub Actions role allowing it to push
  to that public repo

> **First time using ECR Public on this AWS account?** You'll need to claim a
> public alias once via the AWS Console (ECR → Public registries → "Get
> started"). After that, your alias is whatever you picked (probably `aies`).
> The `ECR_PUBLIC_ALIAS: aies` env var in our CI workflow assumes this. Change
> it if your alias differs.

---

## Step 3 — verify

After `terraform apply` of both steps, from anywhere:

```bash
aws ecr describe-repositories --region ap-southeast-5 \
  --repository-names aies/serverlessgpu-gateway \
  --query 'repositories[0].repositoryUri'
# → 865626945255.dkr.ecr.ap-southeast-5.amazonaws.com/aies/serverlessgpu-gateway

aws ecr-public describe-repositories --region us-east-1 \
  --repository-names serverlessgpu-pi-worker \
  --query 'repositories[0].repositoryUri'
# → public.ecr.aws/<your-alias>/serverlessgpu-pi-worker
```

Then push to the Serverless-GPU repo's `main` branch → CI's `images` job
should now succeed and publish both images.

---

## What happens at runtime

```
helm install …                                EKS cluster
                                                  │
                                                  │ pulls from private ECR
                                                  ▼  (node IAM role, no secret)
                                       865626945255.dkr.ecr.ap-southeast-5.amazonaws.com/
                                            aies/serverlessgpu-gateway:<sha>

PI dashboard creates a custom template
referencing →                          public.ecr.aws/aies/serverlessgpu-pi-worker:latest

When a worker is provisioned, PI's
network pulls this image without
needing any AWS credentials.
```

That's it. One IAM role, two ECR registries, no GHCR involvement.

---

## Rollback / removal

To remove these resources:

1. Remove the entry from `ecr_definitions` in `terraform.tfvars`
2. Remove the `aws_ecrpublic_repository` and `aws_iam_role_policy.ecr_public_push` resources
3. `terraform apply`

ECR repos with images in them won't delete cleanly — set `force_delete = true`
on the resources if you actually want to nuke them. Otherwise empty them
first.
