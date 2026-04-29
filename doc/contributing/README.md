# Contributing

```{note}
We welcome contributions and suggestions!

See our [GitHub issues with the "help wanted"](https://github.com/microsoft/PyRIT/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22help%20wanted%22), and ["good first issue"](https://github.com/microsoft/PyRIT/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22good%20first%20issue%22) tags for pre-scoped items to contribute.
```

For a more detailed overview on how to contribute read below.

## Ways to contribute

```{mermaid}
%%{
  init: {
    'theme': 'base',
    'themeVariables': {
      'lineColor': '#f39e3d',
      'secondaryColor': '#f6bb77',
      'primaryBorderColor': '#f39e3d'
    }
  }
}%%
flowchart TB
  Start(You are here!) --> PathSplit{Do you already have a specific problem/feature request/bug that you want to fix/address?}
  PathSplit -- "&nbsp;&nbsp;yes&nbsp;&nbsp;" --> IssueCheck
  IssueCheck{Is there a GitHub issue for this problem?} -- "&nbsp;&nbsp;yes&nbsp;&nbsp;" --> IssueExists
  IssueCheck -- "&nbsp;&nbsp;no&nbsp;&nbsp;" --> CreateIssue
  CreateIssue(Create a GitHub issue) --> IssueCuration
  IssueCuration(Wait for response from maintainers) --> IssueExists
  IssueExists{Is the issue marked as *help wanted* and/or *good first issue*} -- "&nbsp;&nbsp;yes&nbsp;&nbsp;" --> IssueClaimedCheck
  IssueExists -- "&nbsp;&nbsp;no&nbsp;&nbsp;" --> IssueNotReady(Feel free to comment on the issue to raise awareness.)
  IssueClaimedCheck{Is the issue already claimed by someone?} -- "&nbsp;&nbsp;yes&nbsp;&nbsp;" --> IssueClaimed(If there hasn't been progress for a while feel free to comment on the issue. In the meantime, feel free to pick up a different issue.)
  IssueClaimedCheck -- "&nbsp;&nbsp;no&nbsp;&nbsp;" --> ClaimIssue(Claim the issue for yourself by commenting *I would like to take this.* If anything is unclear ask on the issue.)
  ClaimIssue --> SubmitPR(Submit pull request)
  SubmitPR -- "&nbsp;&nbsp;PR feedback&nbsp;&nbsp;" --> PR(Address feedback)
  PR -- "&nbsp;&nbsp;PR feedback&nbsp;&nbsp;" --> PR
  PR -- "&nbsp;&nbsp;maintainer merges PR&nbsp;&nbsp;" ----> Merged(PR completed! Thank you!)
  PathSplit -- "&nbsp;&nbsp;no&nbsp;&nbsp;" --> ContribOrUse{Do you want to contribute right away or use PyRIT first?}
  ContribOrUse -- "&nbsp;&nbsp;use&nbsp;&nbsp;" --> UseIssue{Is something not working? Do you want additional features?}
  UseIssue -- "&nbsp;&nbsp;yes&nbsp;&nbsp;" --> IssueCheck
  UseIssue -- "&nbsp;&nbsp;no&nbsp;&nbsp;" --> NoProblems(Nice! Let us know how it goes.)
  ContribOrUse -- "&nbsp;&nbsp;contribute&nbsp;&nbsp;" --> CheckIssueList(Check our GitHub issues with tags *help wanted* and *good first issue*)
  CheckIssueList -- "&nbsp;&nbsp;find interesting issue&nbsp;&nbsp;" --> IssueClaimedCheck
```

Contributions come in many forms such as *writing code* or *adding examples*.

It can be just as useful to use the package and [file issues](https://github.com/microsoft/PyRIT/issues) for *bugs* or potential *improvements* as well as *missing or inadequate documentation*. Most open source developers start out with small contributions like this as it is a great way to learn about the project and the associated processes. I you already have a problem that you want to solve we recommend opening an issue before submitting a pull request. Opening the issue can help in clarifying the approach to addressing the problem. In some cases, this saves the author from spending time on a pull request that cannot be accepted. If you want to contribute but you're not sure in which way, head on over to the issues marked with ["help wanted"](https://github.com/microsoft/PyRIT/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22help%20wanted%22) or ["good first issue"](https://github.com/microsoft/PyRIT/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22good%20first%20issue%22) and see if one of them sounds interesting. If you're still not sure, join our [Discord server](https://discord.gg/9fMpq3tc8u) and ask for help. One of the maintainers will respond.

For new features, it's important to understand our basic [architecture](../code/framework.md). This can help you get on the right track to contributing.

Importantly, all pull requests are expected to pass the various test/build pipelines. A pull request can only be merged by a maintainer (an AI Red Team member) who will check that tests were added (or updated) and relevant documentation was updated as necessary. We do not provide any guarantees on response times, although team members will do their best to respond within a business day.

In some cases, pull requests don't move forward. This might happen because the author is no longer available to contribute, and/or because the proposed change is no longer relevant. If the change is still relevant maintainers will check in with the author on the pull request. If there is no response within 14 days (two weeks) maintainers may assign someone else to continue the work (assuming the CLA has been accepted). In rare cases, maintainers might not be able to wait with reassigning the work. For example, if a particular change is on the critical path for other planned changes. Ideally, such changes are handled by an AI Red Team member so that this problem doesn't occur in the first place.

## Contributor License Agreement and Code of Conduct

This project welcomes contributions and suggestions.
Most contributions require you to agree to a Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us the rights to use your contribution.
For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide a CLA and decorate the PR appropriately (e.g., status check, comment).
Simply follow the instructions provided by the bot.
You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.
