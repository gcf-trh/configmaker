#!/usr/bin/env python

import sys
import os
import re
import glob
import argparse
import pandas as pd
import yaml
import logging
import json

logger = logging.getLogger('GCF-configmaker')
logger.setLevel(logging.WARNING)



SEQUENCERS = {
    'NB501038' : 'NextSeq 500',
    'SN7001334' : 'HiSeq 2500',
    'K00251' : 'HiSeq 4000',
    'M02675' : 'MiSeq NTNU',
    'M03942' : 'MiSeq StOlav',
    'M05617' : 'MiSeq SINTEF'
}


class FullPaths(argparse.Action):
    """Expand user- and relative-paths"""
    def __call__(self, parser, namespace, values, option_string=None):
        values = [os.path.abspath(os.path.expanduser(v)) for v in values]
        setattr(namespace, self.dest, values)


def is_dir(dirname):
    """Checks if a path is an actual directory"""
    if not os.path.isdir(dirname):
        msg = "{0} is not a directory".format(dirname)
        raise argparse.ArgumentTypeError(msg)
    else:
        return dirname

def is_valid_gcf_id(arg, patt='GCF-\d{4}-\d{3}'):
    if arg is None:
        return True
    m = re.match(patt, arg)
    if m:
        return m.group().strip()
    else:
        msg = "{0} is not a valid GCF number (format: GCF-YYYY-NNN)".format(arg)
        raise argparse.ArgumentTypeError(msg)

def _match_project_dir(pth, project_id=None):
    
    if project_id:
        for fn in os.listdir(pth):
            if os.path.isdir(os.path.join(pth, fn)) and fn == project_id:
                return os.path.join(pth, fn), project_id
        msg = "{0} is not present in run_folder: {1}".format(project_id, pth)
        raise ValueError(msg)
    else:
        project_dir = None
        for fn in os.listdir(pth):
            if os.path.isdir(os.path.join(pth, fn)) and re.match('GCF-\d{4}-\d{3}', fn):
                if project_dir is not None:
                    raise ValueError('runfolders contain more than one project folders. Use `--project-id` option to choose one.')
                project_dir = os.path.join(pth, fn)
                project_id = fn
        if project_dir:
            return project_dir, project_id
        raise ValueError('failed to identify any valid projects in runfolder: {}'.format(pth))
    
def _match_samplesheet(pth):
    matches = glob.glob(os.path.join(pth, 'SampleSheet.csv'))
    return matches

def inspect_samplesheet(samplesheet, runfolders):
    """
    if --samplesheet is set: Check that file exists and return it.
    else: check that runfolder(s) contain a SampleSheet.csv and return it (them).
    """
    if samplesheet is not None:
        return [samplesheet.name]
    else:
        samplesheets = []
        for pth in runfolders:
            ss = _match_samplesheet(pth)
            for s in ss:
                samplesheets.append(s)
        if len(samplesheets) == 0:
            msg = "Cannot find SampleSheet.csv in {}".format(', '.join(runfolders))
            raise RuntimeError(msg)
        return samplesheets

def get_data_from_samplesheet(fh):
    custom_opts = False
    opts_d = {}
    while True:
        line = fh.readline()
        if not line:
            msg = 'No [data]-section in samplesheet {}'.format(s.name)
            raise RuntimeError(msg)
        if line.startswith('[Data]'):
            return pd.read_csv(fh), opts_d
        elif line.startswith('[CustomOptions]'):
            custom_opts = True
            continue
        elif custom_opts:
            key, val = [i.rstrip() for i in line.split(',')]
            if val.lower() == 'true':
                val = True
            opts_d[key] = val

def get_project_samples_from_samplesheet(samplesheet, runfolders, project_id):
    """
    Return a dataframe containing project samples
    """
    ss = inspect_samplesheet(samplesheet, runfolders)
    df_list = []
    for sheet in ss:
        with open(sheet, 'r') as s:
            data, opts = get_data_from_samplesheet(s)
            df_list.append(data)
    df = pd.concat(df_list)
    df = df[df.Sample_Project == project_id]
    df['Sample_ID'] = df['Sample_ID'].astype(str)
    df = df[['Sample_ID']]
    df = df.drop_duplicates(['Sample_ID'])
    return df, opts

def inspect_dirs(runfolders, project_id=None):
    project_dirs = []
    project_ids = set()
    for pth in runfolders:
        pdir, pid = _match_project_dir(pth, project_id)
        project_dirs.append(pdir)
        project_ids.add(pid)
    if len(project_ids) > 1:
        raise ValueError('runfolders contain more than one GCF project ID. Use `--project-id` option to choose one.')
    elif len(project_ids) == 1:
        project_id = project_ids.pop()
    return project_dirs, project_id

def match_fastq(sample_name, project_dir, rel_path=True):
    """Return fastq files matching a sample name.

    Returns paths relative to project directory
    """
    r1_fastq_files = sorted(glob.glob(os.path.join(project_dir, '**', sample_name + '_*R1*.fastq.gz'), recursive=True))
    r2_fastq_files = sorted(glob.glob(os.path.join(project_dir, '**', sample_name + '_*R2*.fastq.gz'), recursive=True))
    if rel_path:
        r1_fastq_files = [os.path.relpath(x,os.path.dirname(os.path.dirname(project_dir))) for x in r1_fastq_files]
        r2_fastq_files = [os.path.relpath(x,os.path.dirname(os.path.dirname(project_dir))) for x in r2_fastq_files]
    return r1_fastq_files, r2_fastq_files

def find_samples(df, project_dirs):
    sample_dict = {}
    for index, row in df.iterrows():
        s_r1 = []
        s_r2 = []
        for p_pth in project_dirs:
            r1, r2 = match_fastq(row.Sample_ID, p_pth)
            s_r1.extend(r1)
            s_r2.extend(r2)
        pe = 0 if len(s_r2) == 0 else 1
        sample_dict[str(row.Sample_ID)] = {
                'R1': ','.join(s_r1),
                'R2': ','.join(s_r2),
                'paired_end': pe,
                'Sample_ID': row.Sample_ID,
            }

    return sample_dict

def merge_samples_with_submission_form(ssub, sample_dict):
    customer = pd.read_excel(ssub.name, sheet_name=0, skiprows=14)
    customer_column_map = {
        'Unique Sample ID': 'Sample_ID',
        'External ID (optional reference sample ID)': 'External_ID',
        'Sample Group (conditions to be compared)': 'Sample_Group',
        'Comments (optional info that does not fit in other columns)': 'Customer_Comment',
        'Sample biosource (examples: celltype/tissue/FFPE)': 'Sample_Biosource',
        'Project ID': 'Project_ID',
        'Sample type (e.g RNA or DNA or library)': 'Sample_Type',
        'Index1_p7 (If dual indexed libraries are submitted indicate what index sequence is used P7)': 'Index',
        'Index2_p5 (If libraries are submitted  indicate what index sequence is used P75)': 'Index2',
        'Plate location (if samples delivered in 96 well plates)': 'Plate',
        'Sample Buffer': 'Sample_Buffer',
        'Volume (ul)': 'Volume',
        'Quantification Method': 'Quantification_Method',
        'Concentration (ng/ul)': 'Concentration',
        '260/280 ratio': '260/280',
        '260/230 ratio': '260/230',
        }
    customer.rename(columns=customer_column_map, inplace=True)
    remove_cols = ['Concentration', 'Index', 'Index2', 'Sample_Type', 'Plate', 'Sample_Buffer', 'Volume', 'Quantification_Method', 'Concentration', '260/280', '260/230']
    customer.drop(remove_cols, axis=1, inplace=True)

    check_existence_of_samples(sample_dict.keys(), customer)
    lab = pd.read_excel(ssub.name, sheet_name=2)
    lab_column_map = {
            'Concentration (ng/ul)': 'Concentration',
            '260/280 ratio': '260/280',
            '260/230 ratio': '260/230',
            'Comment': 'Lab_Comment'
        }
    lab.rename(columns=lab_column_map, inplace=True)
    lab = lab.drop(['Sample_Name','Project ID','KIT'], axis=1)
    if not lab.empty:
        merge = pd.merge(customer, lab, on='Sample_ID', how='inner')
    else:
        merge = customer
    merge['Sample_ID'] = merge['Sample_ID'].astype(str)
    sample_df = pd.DataFrame.from_dict(sample_dict,orient='index')
    sample_df = sample_df.merge(merge,on='Sample_ID',how='inner')
    sample_df.reset_index()
    sample_df.index = sample_df['Sample_ID']
    sample_df.fillna('',inplace=True)
    s_dict = sample_df.to_dict(orient='index')
    return s_dict

def check_existence_of_samples(samples, df):
    diff = set(samples) - set(df['Sample_ID'].astype(str))
    if diff:
        logger.warning("WARNING: Samples {} are contained in SampleSheet, but not in sample submission form!".format(', '.join(list(diff))))
    diff = set(df['Sample_ID'].astype(str)) - set(samples)
    if diff:
        logger.warning("WARNING: Samples {} are contained in sample submission form, but not in SampleSheet!".format(', '.join(list(diff))))
    return None

def find_read_geometry(runfolders):
    all = set()
    for fn in runfolders:
        stats_fn = os.path.join(fn, 'Stats', 'Stats.json')
        read_geometry = []
        with open(stats_fn) as fh:
            S = json.load(fh)
        for read in S['ReadInfosForLanes'][0]['ReadInfos']:
            if not read['IsIndexedRead']:
                read_geometry.append(read['NumCycles'])
        all.add(':'.join(map(str, read_geometry))) 
    if len(all) > 1:
        raise ValueError('Read geometry mismatch between runfolders. Check Stats.json!')
    return read_geometry

def find_machine(runfolders):
    matches = set()
    for pth in runfolders:
        machine_code = os.path.basename(pth).split('_')[1]
        machine = SEQUENCERS.get(machine_code, '')
        matches.add(machine)
    if len(matches) > 1:
        logger.warning('Multiple sequencing machines identified!')
    return '|'.join(list(matches))
        
def create_default_config(sample_dict, opts, args, project_id=None, fastq_dir=None):
    config = {}
    if project_id:
         config['project_id'] = project_id

    if 'Organism' in opts:
        config['organism'] = opts['Organism']
    if args.organism is not None:
        config['organism'] = args.organism

    if 'Libprep' in opts:
        config['libprepkit'] = opts['Libprep']
    if args.libkit is not None:
        config['libprepkit'] = args.libkit

    config['read_geometry'] = find_read_geometry(args.runfolders)
    config['machine'] = args.machine or find_machine(args.runfolders)
    if fastq_dir:
        config['fastq_dir'] = fastq_dir
    
    config['samples'] = sample_dict

    return config


if __name__ == '__main__':

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-p", "--project-id" , help="Project ID", default=None, type=is_valid_gcf_id)
    parser.add_argument("runfolders", nargs="+", help="Path(s) to flowcell dir(s)", action=FullPaths, type=is_dir)
    parser.add_argument("-s", "--sample-sheet", dest="samplesheet", type=argparse.FileType('r'), help="IEM Samplesheet")
    parser.add_argument("-o", "--output", default="config.yaml", help="Output config file", type=argparse.FileType('w'))
    parser.add_argument("-S", "--sample-submission-form", dest="ssub", type=argparse.FileType('r'), help="GCF Sample Submission Form")
    parser.add_argument("--organism",  help="Organism (if applicable to all samples). Overrides value from samplesheet.")
    parser.add_argument("--libkit",  help="Library preparation kit name. (if applicable for all samples). Overrides value from samplesheet.")
    parser.add_argument("--machine",  help="Sequencer model.")
    parser.add_argument("--create-fastq-dir", action='store_true', help="Create fastq dir and symlink fastq files")
    
    args = parser.parse_args()
    project_dirs, args.project_id = inspect_dirs(args.runfolders, args.project_id)
    s_df, opts = get_project_samples_from_samplesheet(args.samplesheet, args.runfolders, args.project_id)
    sample_dict = find_samples(s_df, project_dirs)

    if args.ssub is None:
        if len(args.runfolders) == 1:
            ssub_fn = os.path.join(args.runfolders[0], 'Sample-Submission-Form.xlsx')
            if os.path.exists(ssub_fn):
                args.ssub = open(ssub_fn, 'rb')
        else:
            raise ValueError('`--sample-submission-form` option is required with multiple runfolders.')
    
    if args.ssub is not None:
        sample_dict = merge_samples_with_submission_form(args.ssub, sample_dict)

    fastq_dir = None
    if args.create_fastq_dir:
        default_fastq_dir = 'data/raw/fastq'
        os.makedirs(default_fastq_dir, exist_ok=True)
        for sample_id in sample_dict.keys():
            for pid in project_dirs:
                r1_src, r2_src = match_fastq(sample_id, pid, rel_path=False)
                r1_dst, r2_dst = match_fastq(sample_id, pid, rel_path=True)
                for src, dst in zip(r1_src, r1_dst):
                    dst = os.path.join(default_fastq_dir, dst)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.symlink(src, dst)
                for src, dst in zip(r2_src, r2_dst):
                    dst = os.path.join(default_fastq_dir, dst)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.symlink(src, dst)
        fastq_dir = default_fastq_dir

    config =  create_default_config(sample_dict, opts, args, project_id=args.project_id, fastq_dir=fastq_dir)

    yaml.dump(config, args.output, default_flow_style=False, sort_keys=False)
