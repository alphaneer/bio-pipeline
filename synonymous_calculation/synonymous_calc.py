#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
%prog.py <protein file> <nucleotide file> <output file>

Author: Brad Chapman, Haibao Tang
Calculate synonymous mutation rates for gene pairs

This does the following:
    1. Fetches a protein pair.
    2. Aligns the protein pair with clustalw
    3. Convert the output to Fasta format.
    4. Use this alignment info to align gene sequences using PAL2NAL 
    5. Run PAML yn00 to calculate synonymous mutation rates.
"""
CLEAN_UP = 0

########### MODIFY THE FOLLOWING PATHS ###########
CLUSTALW_BIN = "~/bin/clustalw2"
PAL2NAL_BIN = "~/bin/pal2nal.pl"
PAML_BIN = "~/bin/yn00"
##################################################

import sys
import os
import os.path as op
from subprocess import Popen 

from Bio import Clustalw
from Bio import SeqIO
from Bio import AlignIO


class AbstractCommandline:
    
    def run(self):
        r = Popen(str(self), shell=True)
        return r.communicate()


class YnCommandline(AbstractCommandline):
    """Little commandline for yn00.
    """
    def __init__(self, ctl_file, command=PAML_BIN):
        self.ctl_file = ctl_file
        self.parameters = []
        self.command = command

    def __str__(self):
        return self.command + " %s >/dev/null" % self.ctl_file
    

class MrTransCommandline(AbstractCommandline):
    """Simple commandline faker.
    """
    def __init__(self, prot_align_file, nuc_file, output_file, command=PAL2NAL_BIN):
        self.prot_align_file = prot_align_file
        self.nuc_file = nuc_file
        self.output_file = output_file
        self.command = command

        self.parameters = []

    def __str__(self):
        return self.command + " %s %s -output paml> %s" % (self.prot_align_file, self.nuc_file, self.output_file)


def main(protein_file, dna_file, output_file):
    output_h = open(output_file, "w")
    output_h.write("name,dS-yn,dN-yn,dS-ng,dN-ng\n")
    work_dir = os.path.join(os.getcwd(), "syn_analysis")
    if not(os.path.exists(work_dir)):
        os.makedirs(work_dir)
    prot_iterator = SeqIO.parse(open(protein_file), "fasta")
    dna_iterator = SeqIO.parse(open(dna_file), "fasta")
    for p_rec_1, p_rec_2, n_rec_1, n_rec_2 in \
            zip(prot_iterator, prot_iterator, dna_iterator, dna_iterator):

        print >>sys.stderr, "--------", p_rec_1.name, p_rec_2.name
        align_fasta = clustal_align_protein(p_rec_1, p_rec_2, work_dir)
        mrtrans_fasta = run_mrtrans(align_fasta, n_rec_1, n_rec_2, work_dir)
        if mrtrans_fasta:
            ds_subs_yn, dn_subs_yn, ds_subs_ng, dn_subs_ng = \
                    find_synonymous(mrtrans_fasta, work_dir)
            if ds_subs_yn is not None:
                pair_name = "%s;%s" % (p_rec_1.name, p_rec_2.name)
                output_h.write("%s,%s,%s,%s,%s\n" % (pair_name, 
                        ds_subs_yn, dn_subs_yn, ds_subs_ng, dn_subs_ng))
                output_h.flush()

def find_synonymous(input_file, work_dir):
    """Run yn00 to find the synonymous subsitution rate for the alignment.
    """
    # create the .ctl file
    ctl_file = os.path.join(work_dir, "yn-input.ctl")
    output_file = os.path.join(work_dir, "nuc-subs.yn")
    ctl_h = open(ctl_file, "w")
    ctl_h.write("seqfile = %s\noutfile = %s\nverbose = 0\n" % 
                (input_file, output_file))
    ctl_h.write("icode = 0\nweighting = 0\ncommonf3x4 = 0\n")
    ctl_h.close()

    cl = YnCommandline(ctl_file)
    print >>sys.stderr, "\tyn00:", cl
    r, e = cl.run()
    ds_value_yn = None
    ds_value_ng = None
    dn_value_yn = None
    dn_value_ng = None
    
    # Nei-Gojobori
    output_h = open(output_file)
    row = output_h.readline()
    while row:
        if row.find("Nei & Gojobori") >=0:
            for x in xrange(5):
                row = output_h.next()
            dn_value_ng, ds_value_ng = row.split('(')[1].split(')')[0].split()
            break
        row = output_h.readline()
    output_h.close()
    
    # Yang
    output_h = open(output_file)
    for line in output_h.xreadlines():
        if line.find("+-") >= 0 and line.find("dS") == -1:
            parts = line.split(" +-")
            ds_value_yn = extract_subs_value(parts[1])
            dn_value_yn = extract_subs_value(parts[0])
    
    if ds_value_yn is None or ds_value_ng is None:
        h = open(output_file)
        print >>sys.stderr, "yn00 didn't work: \n%s" % h.read()

    return ds_value_yn, dn_value_yn, ds_value_ng, dn_value_ng

def extract_subs_value(text):
    """Extract a subsitution value from a line of text.

    This is just a friendly function to grab a float value for Ks and Kn 
    values from the junk I get from the last line of the yn00 file.

    Line:
    2    1    52.7   193.3   2.0452  0.8979  0.0193 0.0573 +- 0.0177 
    2.9732 +- 3.2002

    Parts:
        ['   2    1    52.7   193.3   2.0452  0.8979  0.0193 0.0573', 
         ' 0.0177  2.9732', ' 3.2002\n']
    
    So we want 0.0573 for Kn and 2.9732 for Ks.
    """
    parts = text.split()
    value = float(parts[-1])

    return value
    

def run_mrtrans(align_fasta, rec_1, rec_2, work_dir):
    """Align two nucleotide sequences with mrtrans and the protein alignment.
    """
    try:
        align_file = os.path.join(work_dir, "prot-align.fasta")
        nuc_file = os.path.join(work_dir, "nuc.fasta")
        output_file = os.path.join(work_dir, "nuc-align.mrtrans")
        
        # make the protein alignment file
        align_h = open(align_file, "w")
        align_h.write(str(align_fasta))
        align_h.close()
        # make the nucleotide file
        nuc_h = open(nuc_file, "w")
        SeqIO.write((rec_1, rec_2), nuc_file, "fasta")
        nuc_h.close()
            
        # run the program
        cl = MrTransCommandline(align_file, nuc_file, output_file)
        r, e = cl.run()
        if e is None:
            print >>sys.stderr, "\tpal2nal:", cl
            return output_file
        elif e.read().find("could not translate") >= 0:
            print >>sys.stderr, "***pal2nal could not translate"
            return None
    finally:
        if CLEAN_UP:
            if os.path.exists(nuc_file):
                os.remove(nuc_file)
            if os.path.exists(align_file):
                os.remove(align_file)


def clustal_align_protein(rec_1, rec_2, work_dir):
    """Align the two given proteins with clustalw.
    """
    try:
        fasta_file = os.path.join(work_dir, "prot-start.fasta")
        align_file = os.path.join(work_dir, "prot.aln")
        fasta_h = open(fasta_file, "w")
        SeqIO.write((rec_1, rec_2), fasta_h, "fasta")
        fasta_h.close()
        clustal_cl = Clustalw.MultipleAlignCL(fasta_file, command=CLUSTALW_BIN)
        clustal_cl.set_output(align_file, output_order = 'INPUT')
        clustal_cl.set_type('PROTEIN')
        Clustalw.do_alignment(clustal_cl)
        aln_file = file(clustal_cl.output_file)
        alignment = AlignIO.read(aln_file, "clustal")
        print >>sys.stderr, "\tDoing clustalw alignment: %s" % clustal_cl
        return alignment.format("fasta") 
    finally:
        if CLEAN_UP:
            if os.path.exists(align_file):
                os.remove(align_file)
            if os.path.exists(fasta_file):
                os.remove(fasta_file)


if __name__ == "__main__":

    from optparse import OptionParser

    p = OptionParser(__doc__)
    options, args = p.parse_args()

    if len(args) != 3:
        print >>sys.stderr, "Incorrect arguments"
        sys.exit(p.print_help())

    protein_file, dna_file, output_file = args
    main(protein_file, dna_file, output_file)

