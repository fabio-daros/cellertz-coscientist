IDENTIFIER_COLUMNS = [
    "binder_id",
]

PROVENANCE_COLUMNS = [
    "campaign",
    "campaign_short",
    "target_sequence",
]

SEQUENCE_COLUMNS = [
    "binder_sequence",
]

PRIMARY_LABEL = "binder_by_YSD_1000nM"

SECONDARY_LABELS = [
    "binder_by_YSD_500nM",
    "binder_by_YSD_100nM",
]

EXPLICIT_SAFE_FEATURES = [
    "binder_seq_net_charge",
    "n_hydrophobic_residues",
]

FORBIDDEN_FEATURE_PATTERNS = [
    "ysd",
    "kd",
    "activity",
    "coculture",
    "staining",
    "binder_by_",
    "manuscript",
]

EXPLICIT_EXCLUSIONS = [
    "Average_ligand_iptm",
    "Average_InterfaceAAs",
]

BCMA_FEATURES_V1 = [
    "binder_seq_net_charge",
    "n_hydrophobic_residues",
    "Average_Binder_BetaSheet%",
    "Average_Binder_Energy_Score",
    "Average_Binder_Helix%",
    "Average_Binder_Loop%",
    "Average_Chn1_A_to_Chn2_B_ipSAE",
    "Average_Chn1_B_to_Chn2_A_ipSAE",
    "Average_InterfaceHbondsPercentage",
    "Average_InterfaceUnsatHbondsPercentage",
    "Average_Interface_BetaSheet%",
    "Average_Interface_Helix%",
    "Average_Interface_Hydrophobicity",
    "Average_Interface_Loop%",
    "Average_Interface_SASA_%",
    "Average_LIS",
    "Average_PackStat",
    "Average_Relaxed_Clashes",
    "Average_ShapeComplementarity",
    "Average_Surface_Hydrophobicity",
    "Average_Unrelaxed_Clashes",
    "Average_chains_ptm_0",
    "Average_chains_ptm_1",
    "Average_complex_ipde",
    "Average_complex_iplddt",
    "Average_complex_pde",
    "Average_complex_plddt",
    "Average_confidence_score",
    "Average_dG",
    "Average_dSASA",
    "Average_i_pLDDT",
    "Average_ipSAE",
    "Average_ipSAE_d0chn",
    "Average_ipSAE_d0dom",
    "Average_ipSAE_max",
    "Average_ipSAE_min",
    "Average_ipTM_af",
    "Average_ipTM_d0chn",
    "Average_iptm",
    "Average_monomer_RMSD",
    "Average_monomer_lDDT",
    "Average_n_InterfaceHbonds",
    "Average_n_InterfaceResidues",
    "Average_n_InterfaceUnsatHbonds",
    "Average_pDockQ",
    "Average_pDockQ2",
    "Average_pae_all",
    "Average_pae_chain_a",
    "Average_pae_chain_b",
    "Average_pae_interaction",
    "Average_pair_chains_iptm_0_0",
    "Average_pair_chains_iptm_0_1",
    "Average_pair_chains_iptm_1_0",
    "Average_pair_chains_iptm_1_1",
    "Average_protein_iptm",
    "Average_ptm",
    "Average_ss_pLDDT",
]

assert len(BCMA_FEATURES_V1) == 57
