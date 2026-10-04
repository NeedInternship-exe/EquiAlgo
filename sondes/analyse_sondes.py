"""Estimation de l'etalon du jury a partir des scores indicatifs HxBuddy.

    python sondes/analyse_sondes.py

Modele : etalon = 1[cote R + w_h * heures + w_pg * 1re gen + w_E * eloignee + w_rev * ln(revenu)
                    + bruit logistique d'echelle s > seuil], avec un taux d'octroi q.
Chaque score HxBuddy est un compte exact sur 4 000 candidats. On ajuste les
parametres par maximum de vraisemblance (approximation normale des comptes) :
d'abord B seul, puis en liberant les poids. Aucune decision individuelle n'est
ajustee : seulement 2 a 6 parametres interpretables.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize
from scipy.special import expit
from scipy.stats import chi2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from equite import charger_donnees  # noqa: E402

DOSSIER = Path(__file__).resolve().parent
_, candidats = charger_donnees()
resultats = pd.read_csv(DOSSIER / 'resultats_hxbuddy.csv')
SONDES = {r.sonde: pd.read_csv(DOSSIER / r.fichier)['decision_octroi'].to_numpy() for r in resultats.itertuples()}
COMPTES = {r.sonde: int(round(r.exactitude * len(candidats))) for r in resultats.itertuples()}

R = candidats['cote_r_equivalent'].to_numpy()
VARIABLES = np.column_stack([
    candidats['heures_travail_semaine'] - 10.6,
    candidats['premiere_generation_universitaire'],
    candidats['eloignee'],
    candidats['log_revenu'] - 11.03,
])
NOMS = ['poids heures', 'poids 1re gen', 'poids eloignee', 'poids ln(revenu)', 'bruit', 'taux etalon']
DEPART = np.array([0.146, 0.0, 0.0, 0.0, np.log(0.3), 0.40])


def probabilites(theta):
    """P(etalon = 1) pour chaque candidat."""
    score = R + VARIABLES @ theta[:4]
    echelle, taux = np.exp(theta[4]), theta[5]
    seuil = brentq(lambda t: expit((score - t) / echelle).mean() - taux, score.min() - 50, score.max() + 50)
    return expit((score - seuil) / echelle)


def moins_log_vraisemblance(theta):
    if not 0.3 < theta[5] < 0.5:
        return 1e9
    pi = probabilites(theta)
    b = SONDES['B']
    nll = (COMPTES['B'] - (b * pi + (1 - b) * (1 - pi)).sum()) ** 2 / (2 * (pi * (1 - pi)).sum())
    for nom, p in SONDES.items():
        if nom == 'B':
            continue
        diff = p != b
        juste = p[diff] * pi[diff] + (1 - p[diff]) * (1 - pi[diff])
        moyenne, variance = (2 * juste - 1).sum(), (4 * juste * (1 - juste)).sum() + 1e-9
        nll += (COMPTES[nom] - COMPTES['B'] - moyenne) ** 2 / (2 * variance) + 0.5 * np.log(variance)
    return nll


def ajuster(libres):
    def objectif(x):
        theta = DEPART.copy()
        theta[libres] = x
        return moins_log_vraisemblance(theta)

    meilleur = min((minimize(objectif, np.r_[DEPART[libres][:-2], np.log(s0), 0.40], method='Nelder-Mead',
                             options={'xatol': 1e-4, 'fatol': 1e-4, 'maxiter': 4000}) for s0 in (0.2, 0.3, 0.4)),
                   key=lambda r: r.fun)
    theta = DEPART.copy()
    theta[libres] = meilleur.x
    return meilleur.fun, theta


if __name__ == '__main__':
    modeles = {'B (bruit et taux libres)': [4, 5], '+ poids des heures': [0, 4, 5], '+ 1re gen, region, revenu': [0, 1, 2, 3, 4, 5]}
    ajustes = {nom: ajuster(libres) for nom, libres in modeles.items()}
    reference = ajustes['B (bruit et taux libres)'][0]
    for (nom, (nll, theta)), libres in zip(ajustes.items(), modeles.values()):
        ddl = len(libres) - 2
        p = chi2.sf(2 * (reference - nll), ddl) if ddl else float('nan')
        valeurs = ', '.join(f'{NOMS[i]} {np.exp(theta[i]) if i == 4 else theta[i]:.3f}' for i in libres)
        print(f'{nom:28s} -logL {nll:7.3f}  p vs B = {p:.2f}  |  {valeurs}')
