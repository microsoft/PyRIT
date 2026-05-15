# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Lightweight printer module for displaying attack, scenario, and scorer results.

This module contains abstract base classes with all formatting logic.
Data-fetching operations (conversations, scores, scorer metrics) are abstract
methods that must be implemented by subclasses.

Framework users: use the concrete implementations in pyrit.executor.attack.printer
and pyrit.scenario.printer which fetch data via CentralMemory.

Thin clients: subclass the bases here and implement abstract methods via REST calls.
"""
