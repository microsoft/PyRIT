# 1. Contribute with Git

In this guide, there are two ways to set up your fork for PyRIT: one using [Approach 1: Using GitHub CLI](#approach-1-using-github-cli), and one with [Approach 2: Without GitHub CLI](#approach-2-without-github-cli).

## Step 1: Fork
### Approach 1: Using GitHub CLI
You will need to install [GitHub CLI](https://cli.github.com/)
```bash
gh repo fork microsoft/PyRIT --clone=true
```
This command forks, clones, and sets the new repo as `origin`, while the original repo is automatically set as `upstream`.

**(OPTIONAL)** - to pull in the changes from **microsoft/PyRIT** into your forked repo:
```bash
# Fetches changes from microsoft/PyRIT
git fetch upstream

# Merge changes into your main
git checkout main
git merge upstream/main

# Push updates to your fork
git push origin main
```

### Approach 2: Without GitHub CLI
[Fork](https://github.com/microsoft/PyRIT/fork) the repo from the main branch. By default, forks are named the same as their upstream repository. This will create a new repo called `GITHUB_USERNAME/PyRIT` (where `GITHUB_USERNAME` is a variable for your GitHub username).

Add this new repo locally wherever you cloned PyRIT
```bash
# to see existing remotes
git remote -v

# add your fork as a remote named `REMOTE_NAME`
git remote add REMOTE_NAME https://github.com/GITHUB_USERNAME/PyRIT.git
```

## Step 2: Make changes

To add your contribution to the repo, the flow typically looks as follows:

```bash
git checkout main
git pull # pull from origin
git checkout -b mybranch

... # make changes

git add .
git commit -m "changes were made"
git push --set-upstream origin mybranch # For any subsequent push, just do 'git push'
```

## Step 3: Create PR
After pushing changes, you'll see a link to create a PR:
```
remote: Create a pull request for 'mybranch' on GitHub by visiting:
remote:      https://github.com/GITHUB_USERNAME/PyRIT/pull/new/mybranch
```

See more on [creating a pull request from a fork](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request-from-a-fork).

## Step 4: PR review

After you create a pull request, a member of the core team will review it. A PR needs to be approved by a member of the core team before it's merged.

If a PR has been sitting for over a month waiting on the author, then the core team may opt to close it (but you are always free to re-open).
