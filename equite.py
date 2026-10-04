"""Outils partages du defi EquiAlgo.

Chargement des donnees, definition des groupes, modele structurel du comite
historique, score de merite corrige et metriques d'equite.

Idee centrale
-------------
Le comite historique se comporte comme une regression logistique :

    logit P(octroi) = b_R * cote_R + b_rev * ln(revenu) + b_h * heures + ... + b_E * Eloignee

Le terme b_E * Eloignee est une penalite fixe appliquee aux regions eloignees.
Si on retire simplement la colonne region, les proxys (distance, code postal,
heures, revenu) reconstruisent cette penalite. On fait donc l'inverse : on
garde l'indicateur de region *pendant l'estimation*, pour qu'il absorbe la
penalite au lieu de la laisser fuir dans les proxys, puis on le retire du
score. Le score final ne lit jamais la region au moment de decider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

RACINE = Path(__file__).resolve().parent
DONNEES = RACINE / 'data'

ELOIGNEES = ['Bas-Saint-Laurent', 'Cote-Nord', 'Gaspesie-Iles-de-la-Madeleine']
CENTRES = ['Montreal', 'Capitale-Nationale']

# Plage budgetaire imposee sur les 4 000 candidats.
BUDGET_MIN, BUDGET_MAX = 0.36, 0.44
TAUX_CIBLE = 0.40

# Variables qui entrent dans le merite. Le revenu familial et la region sont
# estimes dans le modele du comite (comme controles) mais exclus du score :
# voir audit_rapport.ipynb, section 6.
VARIABLES_MERITE = ('cote_r_equivalent', 'heures_travail_semaine')


def charger_donnees() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retourne (historique, candidats) avec les colonnes derivees utiles."""
    historique = preparer(pd.read_csv(DONNEES / 'donnees_demandes.csv'))
    candidats = preparer(pd.read_csv(DONNEES / 'candidats_evaluation.csv'))
    return historique, candidats


def preparer(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['eloignee'] = df['region_administrative'].isin(ELOIGNEES).astype(int)
    df['groupe'] = np.where(df['eloignee'] == 1, 'Eloignee', 'Centre')
    df['log_revenu'] = np.log(df['revenu_familial_estime'])
    return df


# ---------------------------------------------------------------------------
# Modele structurel du comite
# ---------------------------------------------------------------------------

COVARIABLES = [
    'cote_r_equivalent',
    'log_revenu',
    'heures_travail_semaine',
    'distance_domicile_campus_km',
    'premiere_generation_universitaire',
]


def matrice(df: pd.DataFrame, avec_region: bool = True) -> pd.DataFrame:
    """Matrice de design : covariables, programme (dummies) et indicateur de region."""
    X = df[COVARIABLES].astype(float).copy()
    programmes = pd.get_dummies(df['programme_etudes'], prefix='prog', drop_first=True, dtype=float)
    programmes = programmes.reindex(
        columns=['prog_Genie', 'prog_Sante', 'prog_Sciences', 'prog_Sciences sociales'], fill_value=0.0
    )
    X = pd.concat([X, programmes], axis=1)
    if avec_region:
        X['eloignee'] = df['eloignee'].astype(float)
    return sm.add_constant(X, has_constant='add')


@dataclass
class ModeleComite:
    """Regression logistique du comite, region incluse pour isoler sa penalite."""

    variables_merite: tuple[str, ...] = VARIABLES_MERITE
    resultat: object = field(default=None, repr=False)

    def fit(self, df: pd.DataFrame) -> 'ModeleComite':
        self.resultat = sm.Logit(df['decision_octroi'], matrice(df)).fit(disp=0)
        return self

    @property
    def coefficients(self) -> pd.Series:
        return self.resultat.params

    @property
    def penalite_logit(self) -> float:
        """Effet direct d'etre en region eloignee, en unites logit (negatif = penalite)."""
        return float(self.coefficients['eloignee'])

    @property
    def penalite_points_r(self) -> float:
        """La meme penalite exprimee en points de cote R."""
        return self.penalite_logit / float(self.coefficients['cote_r_equivalent'])

    def logit_comite(self, df: pd.DataFrame) -> np.ndarray:
        """Score du comite tel qu'il a ete (biais compris)."""
        return matrice(df).values @ self.coefficients.values

    def score_merite(self, df: pd.DataFrame) -> np.ndarray:
        """Score de merite : seulement les variables legitimes, sans region ni proxy."""
        b = self.coefficients
        return sum(b[v] * df[v].to_numpy(dtype=float) for v in self.variables_merite)

    def score_corrige(self, df: pd.DataFrame, lam: float) -> np.ndarray:
        """Interpolation entre le comite (lam=0) et le merite (lam=1).

        lam controle la part du biais retiree : la penalite regionale et les
        termes non retenus dans le merite (revenu, distance, programme...).
        C'est le parametre qu'on balaie pour tracer le front de Pareto.
        """
        comite = self.logit_comite(df) - self.coefficients['const']
        return (1 - lam) * comite + lam * self.score_merite(df)


def selection_budget(score: np.ndarray, taux: float = TAUX_CIBLE) -> np.ndarray:
    """Accorde la bourse aux `taux` meilleurs scores (un seul seuil, aveugle a la region).

    Exactement round(taux * n) octrois : les egalites au seuil sont departagees
    par l'ordre des lignes, ce qui garde le resultat deterministe.
    """
    score = np.asarray(score, dtype=float)
    ordre = np.argsort(-score, kind='stable')
    decisions = np.zeros(len(score), dtype=int)
    decisions[ordre[: int(round(taux * len(score)))]] = 1
    return decisions


# ---------------------------------------------------------------------------
# Metriques
# ---------------------------------------------------------------------------

def taux_par_groupe(decisions: np.ndarray, groupes: np.ndarray) -> pd.Series:
    return pd.Series(decisions).groupby(np.asarray(groupes)).mean()


def ecart_parite(decisions, groupes) -> float:
    t = taux_par_groupe(decisions, groupes)
    return float(t.max() - t.min())


def ecart_egalite_chances(decisions, reference, groupes) -> float:
    """Ecart de taux de vrais positifs entre groupes, par rapport a `reference`."""
    decisions, reference, groupes = map(np.asarray, (decisions, reference, groupes))
    tpr = [decisions[(groupes == g) & (reference == 1)].mean() for g in np.unique(groupes)]
    return float(max(tpr) - min(tpr))


def ecart_egalite_chances_signe(decisions, reference, groupes) -> float:
    """TPR Centre - TPR Eloignee : positif si le Centre est favorise, negatif si on sur-corrige."""
    decisions, reference, groupes = map(np.asarray, (decisions, reference, groupes))
    tpr = {g: decisions[(groupes == g) & (reference == 1)].mean() for g in ('Centre', 'Eloignee')}
    return float(tpr['Centre'] - tpr['Eloignee'])


def ratio_impact(decisions, groupes) -> float:
    """Regle des quatre cinquiemes : taux du groupe le moins favorise / taux du plus favorise."""
    t = taux_par_groupe(decisions, groupes)
    return float(t.min() / t.max())


def tableau_equite(decisions, reference, groupes) -> dict[str, float]:
    decisions, reference = np.asarray(decisions), np.asarray(reference)
    return {
        'taux_octroi': float(decisions.mean()),
        'exactitude': float((decisions == reference).mean()),
        'ecart_parite': ecart_parite(decisions, groupes),
        'ecart_egalite_chances': ecart_egalite_chances(decisions, reference, groupes),
        'ratio_impact': ratio_impact(decisions, groupes),
    }


def bootstrap_ic(fonction, *colonnes, n: int = 1000, graine: int = 0) -> tuple[float, float]:
    """Intervalle de confiance a 95 % par bootstrap non parametrique."""
    rng = np.random.default_rng(graine)
    colonnes = [np.asarray(c) for c in colonnes]
    taille = len(colonnes[0])
    valeurs = []
    for _ in range(n):
        idx = rng.integers(0, taille, taille)
        valeurs.append(fonction(*(c[idx] for c in colonnes)))
    return tuple(np.percentile(valeurs, [2.5, 97.5]))
