import pandas as pd
import datetime
import time
import json
import os
from sample_uploader.utils.sample_utils import (
    get_sample_service_url,
    get_sample,
    save_sample,
    generate_user_metadata,
    generate_controlled_metadata,
    update_acls
)
from sample_uploader.utils.verifiers import verifiers
from sample_uploader.utils.parsing_utils import upload_key_format
# from sample_uploader.utils.mappings import shared_fields

# These columns should all be in lower case.
REGULATED_COLS = ['name', 'id', 'parent_id']


def verify_columns(
    df,
    column_verification_map
):
    """"""
    cols = df.columns
    for col in cols:
        if column_verification_map.get(col):
            func_str, args = column_verification_map.get(col)
            func = verifiers.get(func_str)
            if not func:
                raise ValueError(f"no such verifying function {func_str}")
            try:
                func(df[col], *args)
            except Exception as err:
                raise ValueError(f"error parsing column \"{col}\" - {err}")
        # else:
        #     raise ValueError(f"column {col} not supported in input format.")


def validate_params(params):
    if not params.get('sample_file'):
        raise ValueError(f"sample_file argument required in params: {params}")
    if not params.get('workspace_name'):
        raise ValueError(f"workspace_name argument required in params: {params}")
    sample_file = params.get('sample_file')
    if not os.path.isfile(sample_file):
        # try prepending '/staging/' to file and check then
        if os.path.isfile(os.path.join('/staging', sample_file)):
            sample_file = os.path.join('/staging', sample_file)
        else:
            raise ValueError(f"input file {sample_file} does not exist.")
    ws_name = params.get('workspace_name')
    return sample_file


def load_file(
    sample_file,
    header_index,
    date_columns
):
    """"""
    # def find_date_cols(sample_file, date_columns, sep=','):
    #     with open(sample_file) as f:
    #         for i in range(header_index + 1):
    #             col_line = f.readline()
    #     cols = [c.lower() for c in col_line.split(sep)]
    #     new_dcs = []
    #     for dc in date_columns:
    #         if dc.lower() in cols:
    #             new_dcs.append(dc)
    #     return new_dcs
    if sample_file.endswith('.tsv'):
        # dcs = find_date_cols(sample_file, date_columns, sep="\t")
        # df = pd.read_csv(sample_file, sep="\t", parse_dates=dcs, header=header_index)
        df = pd.read_csv(sample_file, sep="\t", header=header_index)
    elif sample_file.endswith('.csv'):
        # dcs = find_date_cols(sample_file, date_columns, sep=",")
        # df = pd.read_csv(sample_file, parse_dates=dcs, header=header_index)
        df = pd.read_csv(sample_file, header=header_index)
    elif sample_file.endswith('.xls') or sample_file.endswith('.xlsx'):
        df = pd.read_excel(sample_file, header=header_index)
    else:
        raise ValueError(f"File {os.path.basename(sample_file)} is not in "
                         f"an accepted file format, accepted file formats "
                         f"are '.xls' '.csv' '.tsv' or '.xlsx'")
    return df


def produce_samples(
    df,
    cols,
    column_groups,
    column_unit_regex,
    sample_url,
    token,
    existing_samples
):
    """"""
    samples = []
    existing_sample_names = {sample['name']: sample for sample in existing_samples}
    for idx, row in df.iterrows():
        if row.get('id'):
            # first we check if a 'kbase_sample_id' column specified.
            kbase_sample_id = None
            if row.get('kbase_sample_id'):
                kbase_sample_id = str(row['kbase_sample_id'])
            # use name field as name, if there is non-reuse id.
            if row.get('name'):
                name = str(row['name'])
            else:
                name = str(row['id'])
            parent = str(row['parent_id'])
            sample = {
                'node_tree': [{
                    "id": str(row['id']),
                    "parent": None,
                    "type": "BioReplicate",
                    "meta_controlled": generate_controlled_metadata(
                        row,
                        column_groups
                    ),
                    "meta_user": generate_user_metadata(
                        row,
                        cols,
                        column_groups,
                        column_unit_regex
                    )
                }],
                'name': name,
            }
            if kbase_sample_id:
                query_sample = get_sample({"id": kbase_sample_id}, sample_url, token)
                if query_sample == sample:
                    continue
                sample_id, sample_ver = save_sample(sample, sample_url, token, previous_version=query_sample)
                if name in existing_sample_names:
                    existing_sample_names.pop(name)
            # Save sample
            elif name in existing_sample_names:
                # Here we compare the existing sample to the newly formed one.
                #   if they are the same, we don't save a new version.
                if existing_sample_names[name] == sample:
                    continue
                sample_id, sample_ver = save_sample(sample, sample_url, token, previous_version=existing_sample_names[name])
                existing_sample_names.pop(name)
            else:
                sample_id, sample_ver = save_sample(sample, sample_url, token)


            samples.append({
                "id": sample_id,
                "name": name,
                "version": sample_ver
            })
            # check input for any reason to update access control list
            # should have a "writer", "reader", "admin" entry
            writer = row.get('writer')
            reader = row.get('reader')
            admin  = row.get('admin')
            if writer or reader or admin:
                acls = {
                    "reader": [r for r in reader],
                    "writer": [w for w in writer],
                    "admin": [a for a in admin]
                }
                update_acls(sample_url, sample_id, acls, token)
        else:
            raise RuntimeError(f"{row['Id']} evaluates as false")
    # add the missing samples from existing_sample_names
    samples += [existing_sample_names[key] for key in existing_sample_names]
    return samples


def import_samples_from_file(
    params,
    sw_url,
    token,
    column_verification_map,
    column_mapping,
    column_groups,
    date_columns,
    column_unit_regex,
    input_sample_set,
    header_index
):
    """
    import samples from '.csv' or '.xls' files in SESAR  format
    """
    # verify inputs
    sample_file = validate_params(params)
    ws_name = params.get('workspace_name')
    df = load_file(sample_file, header_index, date_columns)
    # change columns to upload format
    df = df.rename(columns={c: upload_key_format(c) for c in df.columns})
    verify_columns(df, column_verification_map)
    df = df.rename(columns=column_mapping)
    # process and save samples
    cols = list(set(df.columns) - set(REGULATED_COLS))
    sample_url = get_sample_service_url(sw_url)
    samples = produce_samples(
        df,
        cols,
        column_groups,
        column_unit_regex,
        sample_url,
        token,
        input_sample_set['samples']
    )
    return {
        "samples": samples,
        "description": params.get('description')
    }
