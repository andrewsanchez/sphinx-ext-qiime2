import ast
import functools
import operator
import os
from pathlib import Path
from typing import Tuple, Union

import docutils

import qiime2  # noqa: F401
import qiime2.sdk.usage as usage
from q2cli.core.usage import CLIUsage
from q2doc.usage.nodes import (
    UsageNode,
    UsageExampleNode,
    UsageDataNode,
    UsageMetadataNode,
    FactoryNode,
)
from qiime2.plugins import ArtifactAPIUsage
from qiime2.sdk.usage import ScopeRecord

from .meta_usage import MetaUsage
from .validation import BlockValidator


def process_usage_blocks(app, doctree, _):
    env = app.builder.env
    os.chdir(env.srcdir)
    for use in MetaUsage:
        use = use.value
        processed_records = []
        for block in env.usage_blocks:
            tree = ast.parse(block['code'])
            block["tree"] = tree
            source = compile(tree, filename="<ast>", mode="exec")
            exec(source)
            new_records = get_new_records(use, processed_records)
            records_to_nodes(use, new_records, block, env)
            update_processed_records(new_records, processed_records)
    update_nodes(doctree, env)


def update_nodes(doctree, env):
    for block, node in zip(env.usage_blocks, doctree.traverse(UsageNode)):
        nodes = block["nodes"]
        node.replace_self(nodes)
    for block, node in zip(env.usage_blocks, doctree.traverse(FactoryNode)):
        result = MetaUsage.execution.value._get_record(node.ref).result
        if isinstance(result, qiime2.metadata.metadata.Metadata):
            metadata_preview = str(result.to_dataframe())
            node.preview = metadata_preview


def get_new_records(use, processed_records) -> Union[Tuple[ScopeRecord], None]:
    new_records = tuple()
    records = use._get_records()
    new_record_keys = [k for k in records.keys() if k not in processed_records]
    if new_record_keys:
        new_records = operator.itemgetter(*new_record_keys)(records)
    return new_records


def update_processed_records(new_records, processed_records):
    refs = [i.ref for i in new_records]
    processed_records.extend(refs)


def factories_to_nodes(block, env):
    node = block["nodes"].pop()
    root, doc_name = get_docname(node)
    base = env.config.base_url.rstrip('/')
    name = f'{node.name}.qza'
    # These files won't actually exist until their respective init data blocks
    # are evaluated by ExecutionUsage.
    relative_url = f'results/{name}'
    absolute_url = f'{base}/{doc_name}/{relative_url}'
    factory_node = FactoryNode(
        relative_url=relative_url,
        absolute_url=absolute_url,
        saveas=name,
        ref=node.name,
    )
    block["nodes"].append(factory_node)


@functools.singledispatch
def records_to_nodes(use, records, block, env) -> None:
    """Transform ScopeRecords into docutils Nodes."""


@records_to_nodes.register(usage.DiagnosticUsage)
def diagnostic(use, records, block, env):
    validator = BlockValidator()
    validator.visit(block['tree'])


@records_to_nodes.register(usage.ExecutionUsage)
def execution(use, records, block, env):
    """Creates download nodes and saves factory results."""
    node = block["nodes"][0]
    root, doc_name = get_docname(node)
    out_dir = Path(env.app.outdir) / doc_name / 'results'
    if not out_dir.exists():
        out_dir.mkdir(parents=True)
    if block["nodes"][0].factory:
        factories_to_nodes(block, env)
    for record in records:
        artifact = record.result
        path = os.path.join(out_dir, f'{record.ref}.qza')
        if record.source == "init_metadata" or "init_data":
            artifact.save(path)


def get_docname(node):
    root, doc_name = [Path(p) for p in Path(node.source).parts[-2:]]
    doc_name = Path(doc_name.stem)
    return root, doc_name


@records_to_nodes.register(CLIUsage)
def cli(use, records, block, env):
    for record in records:
        if record.source == "action":
            example = "".join(use.render())
            node = UsageExampleNode(
                titles=["Command Line"], examples=[example]
            )
            # Break after seeing the first record created by use.action() since
            # we only need to call use.render() once.
            block["nodes"] = [node]
            break


@records_to_nodes.register(ArtifactAPIUsage)
def artifact_api(use, records, block, env):
    for record in records:
        source = record.source
        if source == "init_data":
            data_node = init_data_node(record)
            block['nodes'].append(data_node)
        elif source == "init_metadata":
            metadata_node = init_metadata_node(record)
            block['nodes'].append(metadata_node)
        elif source == "action":
            node = block["nodes"][0]
            example = use.render()
            node.titles.append("Artifact API")
            node.examples.append(example)
            # Break after seeing the first record created by use.action() since
            # we only need to call use.render() once.
            break


def init_data_node(record):
    name = record.ref
    fname = f"{name}.qza"
    artifact = MetaUsage.execution.value._get_record(name).result
    stype = f"{artifact.type}"
    load_statement = f"{name} = qiime2.Artifact.load('{fname}')"
    node = UsageDataNode(stype, load_statement, name=name)
    return node


def init_metadata_node(record):
    name = record.ref
    fname = f"{name}.qza"
    metadata = MetaUsage.execution.value._get_record(name).result
    load_statement = f"{name} = qiime2.Metadata.load('{fname}')"
    metadata_preview = str(metadata.to_dataframe())
    node = UsageMetadataNode(load_statement, metadata_preview, name=name)
    return node
