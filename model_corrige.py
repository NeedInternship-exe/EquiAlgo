"""Modele corrige du defi EquiAlgo.

    python model_corrige.py

1. Estime le modele structurel du comite (region incluse, pour isoler sa penalite).
2. Balaie la force de la correction (lambda) et compare aux outils fairlearn
   (ExponentiatedGradient, ThresholdOptimizer) et a la suppression naive de colonnes.
3. Trace le front de Pareto equite / exactitude -> figures/front_pareto.png
4. Valide la methode sur des donnees semi-synthetiques ou le vrai merite est connu
   -> figures/validation_simulation.png
5. Ecrit predictions.csv (4 000 candidats, taux d'octroi dans [36 %, 44 %]).
"""

from __future__ import annotations

import warnings

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from fairlearn.postprocessing import ThresholdOptimizer
from fairlearn.reductions import DemographicParity, ExponentiatedGradient, TruePositiveRateParity
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from equite import (
    BUDGET_MAX,
    BUDGET_MIN,
    RACINE,
    TAUX_CIBLE,
    ModeleComite,
    charger_donnees,
    ecart_egalite_chances_signe,
    selection_budget,
    tableau_equite,
)

warnings.filterwarnings('ignore')

FIGURES = RACINE / 'figures'
RESULTATS = RACINE / 'resultats'
GRAINE = 42
LAMBDAS = np.round(np.linspace(0, 1.4, 15), 2)  # au-dela de 1 : sur-correction
EPSILONS_EG = [0.2, 0.1, 0.05, 0.02, 0.01, 0.005]
TOLERANCES_TO = [0.2, 0.1, 0.05, 0.02, 0.0]

CATEGORIELLES = ['programme_etudes', 'region_administrative', 'code_postal_3']
NON_FEATURES = ['id_candidat', 'decision_octroi', 'groupe', 'eloignee', 'log_revenu']


def encoder(train: pd.DataFrame, cible: pd.DataFrame, exclure: tuple[str, ...] = ()):
    """Encodage one-hot du carnet de depart, avec exclusion optionnelle de colonnes."""
    def brut(df):
        X = pd.get_dummies(df.drop(columns=NON_FEATURES, errors='ignore'), columns=CATEGORIELLES, dtype=float)
        return X[[c for c in X.columns if not c.startswith(exclure)]] if exclure else X

    X = brut(train)
    return X, brut(cible).reindex(columns=X.columns, fill_value=0.0)


def foret():
    """Le modele de production : meme hyperparametres que baseline_model.ipynb."""
    return RandomForestClassifier(n_estimators=300, min_samples_leaf=20, random_state=GRAINE, n_jobs=-1)


# ---------------------------------------------------------------------------
# Les familles de methodes comparees
# ---------------------------------------------------------------------------

def methodes_naives(train, test, y_train):
    """Le modele de production et la suppression de colonnes sensibles (section 5 du carnet)."""
    variantes = {
        'Production (foret, toutes colonnes)': (),
        'Sans region': ('region_administrative_',),
        'Sans region ni code postal': ('region_administrative_', 'code_postal_3_'),
        'Sans region ni proxys': ('region_administrative_', 'code_postal_3_', 'distance_', 'revenu_', 'heures_'),
    }
    for nom, exclure in variantes.items():
        X, Xt = encoder(train, test, exclure)
        yield nom, 'Naive', np.nan, foret().fit(X, y_train).predict(Xt)


def balayage_lambda(train, test, y_train):
    """Notre methode : correction structurelle d'intensite lambda, budget fixe a 40 %."""
    modele = ModeleComite().fit(train.assign(decision_octroi=y_train))
    for lam in LAMBDAS:
        yield f'Correction structurelle (lambda={lam:.1f})', 'Correction structurelle', lam, \
            selection_budget(modele.score_corrige(test, lam))


def _exponentiated_gradient(train, test, y_train, contrainte, famille, etiquette):
    X, Xt = encoder(train, test, ('region_administrative_', 'code_postal_3_'))
    for eps in EPSILONS_EG:
        eg = ExponentiatedGradient(
            make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
            constraints=contrainte(difference_bound=eps),
            sample_weight_name='logisticregression__sample_weight',
        )
        eg.fit(X, y_train, sensitive_features=train['groupe'])
        # Probabilite d'octroi du classifieur randomise, puis budget de 40 %.
        yield f'{etiquette} (borne={eps})', famille, eps, selection_budget(eg._pmf_predict(Xt)[:, 1])


def balayage_eg_egalite_chances(train, test, y_train):
    """fairlearn : parite des TPR... mesuree contre les etiquettes du comite, donc presque inactive."""
    yield from _exponentiated_gradient(train, test, y_train, TruePositiveRateParity,
                                       'ExponentiatedGradient TPR', 'EG - egalite des chances')


def balayage_eg_parite(train, test, y_train):
    """fairlearn : parite demographique, la contrainte qui mord vraiment sur ces donnees."""
    yield from _exponentiated_gradient(train, test, y_train, DemographicParity,
                                       'ExponentiatedGradient DP', 'EG - parite demographique')


def balayage_threshold_optimizer(train, test, y_train):
    """fairlearn : seuils par groupe sur la foret de production, contre les etiquettes du comite."""
    X, Xt = encoder(train, test)
    rf = foret().fit(X, y_train)
    for tol in TOLERANCES_TO:
        to = ThresholdOptimizer(
            estimator=rf, prefit=True, predict_method='predict_proba',
            constraints='true_positive_rate_parity', objective='accuracy_score', tol=tol,
        )
        to.fit(X, y_train, sensitive_features=train['groupe'])
        yield f'ThresholdOptimizer (tolerance={tol})', 'ThresholdOptimizer', tol, \
            selection_budget(to._pmf_predict(Xt, sensitive_features=test['groupe'])[:, 1])


FAMILLES = [methodes_naives, balayage_lambda, balayage_eg_egalite_chances, balayage_eg_parite,
            balayage_threshold_optimizer]


def comparer(train, test, y_train, y_test, merite_test) -> pd.DataFrame:
    """Evalue toutes les methodes sur `test`, contre le merite et contre le comite."""
    lignes = []
    for famille in FAMILLES:
        for nom, groupe_methode, parametre, decisions in famille(train, test, y_train):
            metriques = tableau_equite(decisions, merite_test, test['groupe'])
            metriques['accord_comite'] = float((decisions == np.asarray(y_test)).mean())
            metriques['ecart_ec_signe'] = ecart_egalite_chances_signe(decisions, merite_test, test['groupe'])
            lignes.append({'methode': nom, 'famille': groupe_methode, 'parametre': parametre, **metriques})
    return pd.DataFrame(lignes)


# ---------------------------------------------------------------------------
# Front de Pareto
# ---------------------------------------------------------------------------

STYLE = {
    'Correction structurelle': dict(color='#1b7f5b', marker='o'),
    'ExponentiatedGradient TPR': dict(color='#c0582b', marker='s'),
    'ExponentiatedGradient DP': dict(color='#d9a21b', marker='D'),
    'ThresholdOptimizer': dict(color='#6a4fb3', marker='^'),
    'Naive': dict(color='#7a7a7a', marker='X'),
}


def tracer_front(resultats: pd.DataFrame, chemin, titre: str, axe_y2: bool = True):
    """Ecart signe d'egalite des chances en abscisse : a gauche de zero, on sur-corrige."""
    panneaux = [('exactitude', 'Exactitude contre le mérite')]
    if axe_y2:
        panneaux.append(('accord_comite', 'Accord avec le comité historique'))
    fig, axes = plt.subplots(1, len(panneaux), figsize=(6.6 * len(panneaux), 5.2), squeeze=False)
    for ax, (col, etiquette) in zip(axes[0], panneaux):
        ax.axvline(0, color='black', lw=0.8, alpha=0.6)
        for famille, style in STYLE.items():
            sous = resultats[resultats['famille'] == famille]
            if sous.empty:
                continue
            if famille == 'Naive':
                ax.plot(sous['ecart_ec_signe'], sous[col], linestyle='none', label='Suppression de colonnes',
                        markersize=8, **style)
                continue
            # Ordre du parametre : de la contrainte la plus lache a la plus stricte.
            sous = sous.sort_values('parametre', ascending=famille == 'Correction structurelle')
            if famille == 'Correction structurelle':
                juste = sous[sous['parametre'] <= 1]
                sur = sous[sous['parametre'] >= 1]
                ax.plot(juste['ecart_ec_signe'], juste[col], label='Correction structurelle (lambda 0 → 1)',
                        markersize=6, **style)
                ax.plot(sur['ecart_ec_signe'], sur[col], ls=':', markersize=4, alpha=0.7,
                        label='Sur-correction (lambda > 1)', **style)
                choix = sous[sous['parametre'] == 1.0]
                ax.plot(choix['ecart_ec_signe'], choix[col], marker='*', markersize=18, color='#1b7f5b',
                        markeredgecolor='black', linestyle='none', label='Soumission (lambda = 1)')
                for _, l in sous[sous['parametre'].isin([0.0, 0.5, 1.4])].iterrows():
                    ax.annotate(f"λ={l['parametre']:.1f}", (l['ecart_ec_signe'], l[col]), textcoords='offset points',
                                xytext=(5, 6), fontsize=8, color=style['color'])
            else:
                ax.plot(sous['ecart_ec_signe'], sous[col], markersize=6, alpha=0.9, label=famille, **style)
        ax.set_xlabel("Écart d'égalité des chances : TPR Centre − TPR Éloignée")
        ax.set_ylabel(etiquette)
        ax.grid(alpha=0.3)
    axes[0][0].legend(loc='lower left', fontsize=8, framealpha=0.95)
    fig.suptitle(titre, fontsize=11)
    fig.tight_layout()
    fig.savefig(chemin, dpi=160)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Validation semi-synthetique
# ---------------------------------------------------------------------------

def validation_simulation(historique: pd.DataFrame, repetitions: int = 5) -> pd.DataFrame:
    """Rejoue le probleme la ou la verite est connue.

    On prend le comite estime comme vrai mecanisme generateur : un merite
    (cote R, heures) et une penalite regionale plus du bruit logistique. On
    tire de nouvelles decisions de comite, on re-entraine chaque methode sur
    ces decisions seulement, et on mesure qui retrouve le vrai merite.
    Cela valide la procedure d'estimation, pas l'hypothese structurelle.
    """
    vrai = ModeleComite().fit(historique)
    merite = selection_budget(vrai.score_merite(historique))
    proba = 1 / (1 + np.exp(-vrai.logit_comite(historique)))
    lignes = []
    for r in range(repetitions):
        rng = np.random.default_rng(100 + r)
        y_sim = pd.Series((rng.random(len(historique)) < proba).astype(int), index=historique.index)
        train, test = train_test_split(historique, test_size=0.3, random_state=r, stratify=y_sim)
        res = comparer(train, test, y_sim.loc[train.index], y_sim.loc[test.index], merite[historique.index.get_indexer(test.index)])
        lignes.append(res.assign(repetition=r))
    return pd.concat(lignes).groupby(['methode', 'famille', 'parametre'], dropna=False, sort=False) \
        .mean(numeric_only=True).reset_index().drop(columns='repetition')


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main():
    FIGURES.mkdir(exist_ok=True)
    RESULTATS.mkdir(exist_ok=True)
    historique, candidats = charger_donnees()

    # 1. Modele structurel sur tout l'historique : sert de reference "merite estime".
    comite = ModeleComite().fit(historique)
    print(f"Penalite regionale : {comite.penalite_logit:.2f} logit = {comite.penalite_points_r:.2f} points de cote R")
    merite_hist = selection_budget(comite.score_merite(historique))

    # 2. Comparaison des methodes sur le meme decoupage que le carnet de depart.
    train, test = train_test_split(historique, test_size=0.3, random_state=GRAINE,
                                   stratify=historique['decision_octroi'])
    resultats = comparer(train, test, train['decision_octroi'], test['decision_octroi'],
                         merite_hist[historique.index.get_indexer(test.index)])
    resultats.to_csv(RESULTATS / 'front_pareto.csv', index=False)
    print(resultats.round(3).to_string(index=False))
    tracer_front(resultats, FIGURES / 'front_pareto.png',
                 'Front de Pareto, test interne (3 000 dossiers), mérite estimé par le modèle structurel')

    # 3. Validation la ou le vrai merite est connu.
    simulation = validation_simulation(historique)
    simulation.to_csv(RESULTATS / 'validation_simulation.csv', index=False)
    tracer_front(simulation, FIGURES / 'validation_simulation.png',
                 'Validation semi-synthétique (5 tirages) : le vrai mérite est connu', axe_y2=False)

    # 4. Soumission : modele ajuste sur tout l'historique, lambda = 1, budget 40 %.
    decisions = selection_budget(comite.score_merite(candidats), TAUX_CIBLE)
    taux = decisions.mean()
    assert BUDGET_MIN <= taux <= BUDGET_MAX, f'taux hors budget : {taux:.3f}'
    pd.DataFrame({'id_candidat': candidats['id_candidat'], 'decision_octroi': decisions}) \
        .to_csv(RACINE / 'predictions.csv', index=False)
    par_groupe = pd.Series(decisions).groupby(candidats['groupe'].values).mean()
    print(f"\npredictions.csv : {len(decisions)} lignes, taux d'octroi {taux:.1%} "
          f"(Centre {par_groupe['Centre']:.1%}, Eloignee {par_groupe['Eloignee']:.1%})")


if __name__ == '__main__':
    main()
