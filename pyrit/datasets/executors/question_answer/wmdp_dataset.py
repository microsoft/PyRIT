# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from datasets import load_dataset

from pyrit.models import (
    QuestionAnsweringDataset,
    QuestionAnsweringEntry,
    QuestionChoice,
)


def fetch_wmdp_dataset(category: Optional[str] = None) -> QuestionAnsweringDataset:
    """
    Fetch WMDP examples and create a QuestionAnsweringDataset.

    Args:
        category (str): The dataset category, one of "cyber", "bio", "chem"

    Returns:
        QuestionAnsweringDataset: A QuestionAnsweringDataset containing the examples.

    Raises:
        ValueError: If the category parameter is not one of the expected values.

    Note:
        For more information and access to the original dataset and related materials, visit:
        https://huggingface.co/datasets/cais/wmdp
    """
    # Determine which subset of data to load
    data_categories = None
    if not category:  # if category is not specified, read in all 3 subsets of data
        data_categories = ["wmdp-cyber", "wmdp-bio", "wmdp-chem"]
    elif category not in ["cyber", "bio", "chem"]:
        raise ValueError(f"Invalid Parameter: {category}. Expected 'cyber', 'bio', or 'chem'")
    else:
        data_categories = ["wmdp-" + category]

    # Read in cybersecurity dataset
    questions_answers = []
    for name in data_categories:
        ds = load_dataset("cais/wmdp", name)
        for i in range(len(ds["test"])):
            # For each question, save the 4 possible choices and their respective index
            choices = []
            for j in range(4):
                c = QuestionChoice(index=j, text=ds["test"]["choices"][i][j])
                choices.append(c)

            entry = QuestionAnsweringEntry(
                question=ds["test"]["question"][i],
                answer_type="int",
                correct_answer=ds["test"]["answer"][i],
                choices=choices,
            )
            questions_answers.append(entry)

    return QuestionAnsweringDataset(
        name="wmdp",
        description="""The WMDP Benchmark: Measuring and Reducing Malicious Use With Unlearning. The Weapons of Mass
        Destruction Proxy (WMDP) benchmark is a dataset of 4,157 multiple-choice questions surrounding hazardous
        knowledge in biosecurity, cybersecurity, and chemical security. WMDP serves as both a proxy evaluation
        for hazardous knowledge in large language models (LLMs) and a benchmark for unlearning methods to remove such
        hazardous knowledge. To guide progress on mitigating risk from LLMs, we develop CUT, a state-of-the-art
        unlearning method which reduces model performance on WMDP while maintaining general language model
        capabilities.""",
        author="""Nathaniel Li and Alexander Pan and Anjali Gopal and Summer Yue and Daniel Berrios and Alice
        Gatti and Justin D. Li and Ann-Kathrin Dombrowski and Shashwat Goel and Long Phan and Gabriel Mukobi
        and Nathan Helm-Burger and Rassin Lababidi and Lennart Justen and Andrew B. Liu and Michael Chen and
        Isabelle Barrass and Oliver Zhang and Xiaoyuan Zhu and Rishub Tamirisa and Bhrugu Bharathi and Adam Khoja
        and Zhenqi Zhao and Ariel Herbert-Voss and Cort B. Breuer and Andy Zou and Mantas Mazeika and Zifan Wang
        and Palash Oswal and Weiran Liu and Adam A. Hunt and Justin Tienken-Harder and Kevin Y. Shih and Kemper
        Talley and John Guan and Russell Kaplan and Ian Steneker and David Campbell and Brad Jokubaitis and
        Alex Levinson and Jean Wang and William Qian and Kallol Krishna Karmakar and Steven Basart and Stephen
        Fitz and Mindy Levine and Ponnurangam Kumaraguru and Uday Tupakula and Vijay Varadharajan and Yan
        Shoshitaishvili and Jimmy Ba and Kevin M. Esvelt and Alexandr Wang and Dan Hendrycks""",
        source="https://huggingface.co/datasets/cais/wmdp",
        questions=questions_answers,
    )
