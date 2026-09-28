from tierA import fair
import ctrl_p2_neuro

MRAC_RBF48 = fair(ctrl_p2_neuro.MRAC_RBF48, 'MRAC_RBF48')
MRAC_RBF96 = fair(ctrl_p2_neuro.MRAC_RBF96, 'MRAC_RBF96')
MRAC_PhysRBF = fair(ctrl_p2_neuro.MRAC_PhysRBF, 'MRAC_PhysRBF')
MRAC_Deep = fair(ctrl_p2_neuro.MRAC_Deep, 'MRAC_Deep')

