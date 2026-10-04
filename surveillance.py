"""Tableau de bord de surveillance d'une cohorte de decisions.

    python surveillance.py                       # demo sur les 4 000 candidats et predictions.csv
    python surveillance.py cohorte.csv decisions.csv [revision.csv]

`revision.csv` (facultatif) contient les etiquettes d'un echantillon revise a
l'aveugle (id_candidat, merite). Sans lui, l'ecart d'egalite des chances ne
peut pas etre mesure, seulement les indicateurs sans etiquette.
Seuils : voir GOUVERNANCE.md, section 3.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_predict
from sklearn.metrics import roc_auc_score

from equite import DONNEES, RACINE, ecart_egalite_chances, preparer, ratio_impact

SEUILS = {
    'ratio_impact_min': 0.80,
    'ecart_taux_max': 0.05,
    'ecart_egalite_chances_max': 0.05,
    'psi_max': 0.20,
    'derive_auc_proxy_max': 0.05,
}
VARIABLES_SUIVIES = ['cote_r_equivalent', 'heures_travail_semaine', 'revenu_familial_estime',
                     'distance_domicile_campus_km', 'premiere_generation_universitaire']
AUC_PROXY_REFERENCE = 0.845  # audit_rapport.ipynb, section 5 : region <- variables non geographiques


def psi(reference: pd.Series, actuel: pd.Series, bins: int = 10) -> float:
    """Population Stability Index sur les deciles de la reference."""
    bornes = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    bornes[0], bornes[-1] = -np.inf, np.inf
    p = np.histogram(reference, bornes)[0] / len(reference) + 1e-6
    q = np.histogram(actuel, bornes)[0] / len(actuel) + 1e-6
    return float(np.sum((q - p) * np.log(q / p)))


def auc_proxy(cohorte: pd.DataFrame) -> float:
    """Capacite des variables non geographiques a reconstituer la region."""
    X = pd.get_dummies(cohorte[['cote_r_equivalent', 'heures_travail_semaine', 'revenu_familial_estime',
                                'premiere_generation_universitaire', 'programme_etudes']], dtype=float)
    p = cross_val_predict(RandomForestClassifier(n_estimators=200, min_samples_leaf=10, random_state=0),
                          X, cohorte['eloignee'], cv=5, method='predict_proba')[:, 1]
    return float(roc_auc_score(cohorte['eloignee'], p))


def tableau_de_bord(cohorte: pd.DataFrame, decisions: np.ndarray, reference: pd.DataFrame,
                    revision: pd.DataFrame | None = None) -> pd.DataFrame:
    groupes = cohorte['groupe'].to_numpy()
    taux = pd.Series(decisions).groupby(groupes).mean()
    lignes = [
        ('ratio_impact', ratio_impact(decisions, groupes), SEUILS['ratio_impact_min'], 'min'),
        ('ecart_taux_octroi', abs(taux['Centre'] - taux['Eloignee']), SEUILS['ecart_taux_max'], 'max'),
    ]
    for v in VARIABLES_SUIVIES:
        lignes.append((f'psi_{v}', psi(reference[v], cohorte[v]), SEUILS['psi_max'], 'max'))
    lignes.append(('derive_auc_proxy', abs(auc_proxy(cohorte) - AUC_PROXY_REFERENCE),
                   SEUILS['derive_auc_proxy_max'], 'max'))
    if revision is not None:
        etiquettes = cohorte[['id_candidat']].merge(revision, how='left')['merite']
        connus = etiquettes.notna().to_numpy()
        lignes.append(('ecart_egalite_chances', ecart_egalite_chances(
            decisions[connus], etiquettes[connus].astype(int), groupes[connus]),
            SEUILS['ecart_egalite_chances_max'], 'max'))

    df = pd.DataFrame(lignes, columns=['indicateur', 'valeur', 'seuil', 'sens'])
    df['alerte'] = np.where(df['sens'] == 'min', df['valeur'] < df['seuil'], df['valeur'] > df['seuil'])
    return df.drop(columns='sens')


def main(argv: list[str]):
    reference = preparer(pd.read_csv(DONNEES / 'donnees_demandes.csv'))
    chemin_cohorte = argv[0] if argv else DONNEES / 'candidats_evaluation.csv'
    chemin_decisions = argv[1] if len(argv) > 1 else RACINE / 'predictions.csv'
    cohorte = preparer(pd.read_csv(chemin_cohorte))
    decisions = cohorte[['id_candidat']].merge(pd.read_csv(chemin_decisions))['decision_octroi'].to_numpy()
    revision = pd.read_csv(argv[2]) if len(argv) > 2 else None

    print(f'Cohorte : {len(cohorte)} dossiers, taux d octroi {decisions.mean():.1%}')
    print(pd.Series(decisions).groupby(cohorte['region_administrative'].values).mean().round(3).to_string())
    print()
    tableau = tableau_de_bord(cohorte, decisions, reference, revision)
    print(tableau.round(3).to_string(index=False))
    print('\nAUCUNE ALERTE' if not tableau['alerte'].any() else '\nALERTE : voir GOUVERNANCE.md, section 4')


if __name__ == '__main__':
    main(sys.argv[1:])
