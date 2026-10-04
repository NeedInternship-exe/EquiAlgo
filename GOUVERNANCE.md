# Gouvernance, éthique et plan de surveillance

Ce document accompagne `audit_rapport.ipynb` (le diagnostic) et `model_corrige.py` (la correction). Il répond à une question : **comment s'assurer que le modèle corrigé reste équitable une fois en production ?**

## 1. Ce que nous avons trouvé, et ce que cela engage

Le comité historique appliquait une **pénalité fixe d'environ 1,6 point de cote R** à toute candidature des régions éloignées. Elle explique 17,6 des 21 points d'écart d'octroi. Il favorisait aussi les familles à **revenu élevé**, ce qui ajoute environ 4,4 points d'écart. Le modèle en production reproduisait fidèlement ces deux biais (exactitude de 88 %).

Deux constats engagent l'institution :

- **Le revenu.** Favoriser les revenus élevés dans une aide financière aux études va à l'encontre de l'objet même du programme. La *condition sociale* figure en outre parmi les motifs de discrimination énumérés à l'article 10 de la *Charte des droits et libertés de la personne* du Québec. Nous recommandons un avis juridique avant toute remise en production d'un modèle qui utiliserait le revenu.
- **La région.** Elle n'est pas un motif énuméré, mais elle est ici un proxy de la condition sociale et de la première génération universitaire. Une pénalité régionale non justifiée expose l'institution à un risque réputationnel et réglementaire.

## 2. Principes de conception retenus

| Principe | Mise en œuvre |
|---|---|
| **La région sert à mesurer, jamais à décider.** | L'indicatrice de région est utilisée pendant l'estimation, pour isoler la pénalité, et en surveillance. Le score final (`1,39 × cote R + 0,20 × heures`) ne la lit pas, ni aucun proxy géographique. |
| **Un seul seuil pour tous.** | Pas de seuil par groupe. Deux candidats au dossier identique reçoivent la même décision, quelle que soit leur région. |
| **Un mérite explicite et discutable.** | Les variables du mérite sont listées et justifiées (audit, section 7). Les modifier est une décision de politique, qui relève du comité d'éthique et non de l'équipe de données. |
| **Budget respecté.** | 40 % d'octroi, dans la plage de 36 à 44 % fixée. |
| **Un humain dans la boucle.** | Le modèle recommande. Les cas limites et les contestations sont tranchés par une personne. |

## 3. Plan de surveillance

`surveillance.py` calcule ces indicateurs pour chaque cohorte. Les seuils sont des points de départ, à recalibrer après la première année.

| # | Indicateur | Ce qu'il détecte | Fréquence | Seuil d'alerte | Responsable |
|---|---|---|---|---|---|
| 1 | Taux d'octroi par région et par bloc, ratio 4/5 | Retour d'un écart d'octroi | Chaque cohorte | Ratio < 0,80, ou écart entre blocs qui s'éloigne de plus de 5 points de l'écart de mérite attendu (≈ 0) | Équipe de données |
| 2 | **Écart d'égalité des chances** sur un échantillon de contrôle | Le critère principal : les méritants des deux blocs ont-ils la même chance ? | Trimestriel | > 0,05, ou un IC à 95 % qui exclut 0 | Comité de révision indépendant |
| 3 | Écart d'égalité des chances par région (5) et par quintile de revenu | Biais caché sous la moyenne : une région ou les bas revenus | Semestriel | > 0,08 dans une cellule de plus de 100 méritants | Équipe de données |
| 4 | Dérive des variables (PSI) | Changement de population, par exemple une nouvelle région ou un nouveau formulaire | Mensuel | PSI > 0,20 | Équipe de données |
| 5 | Capacité des variables à reconstituer la région (AUC proxy) | Un nouveau proxy apparaît, ou une variable change de sens | Mensuel | Variation de plus de 0,05 par rapport à 0,845 | Équipe de données |
| 6 | Contestations et renversements par région | Erreurs que les indicateurs ne voient pas | Semestriel | Taux de renversement qui diffère du simple au double entre blocs | Ombudsman |
| 7 | Heures de travail déclarées : distribution et taux de vérification | Manipulation d'une variable valorisée par le score | Chaque cohorte | Hausse de plus de 1 h de la moyenne, ou plus de 5 % d'écarts à la vérification | Service des bourses |

### L'échantillon de contrôle (indicateur 2)

L'égalité des chances exige de savoir qui est méritant, et `decision_octroi` ne le dit pas : c'est la leçon de l'audit. À chaque trimestre :

1. On tire **5 % des dossiers**, en sur-échantillonnant les régions éloignées pour avoir au moins 150 dossiers par bloc.
2. Un **comité indépendant** les révise **à l'aveugle** : sans nom, région, code postal ni distance.
3. On compare ses verdicts aux décisions du modèle et on calcule l'écart de TPR avec un intervalle de confiance par bootstrap.

Cet échantillon sert aussi à vérifier que la définition du mérite reste alignée avec le jugement humain.

## 4. Réponse aux alertes

| Niveau | Déclencheur | Action |
|---|---|---|
| **Vigilance** | Un indicateur mensuel (4, 5 ou 7) dépasse son seuil | Analyse sous deux semaines, note au registre du modèle |
| **Alerte** | Indicateur 1 ou 3 dépassé | Révision humaine de **tous les cas limites** (± 0,5 point de cote R autour du seuil) de la cohorte, audit ciblé |
| **Arrêt** | Indicateur 2 dépassé deux trimestres de suite, ou ratio 4/5 < 0,7 | Retour à la décision humaine assistée. Ré-estimation et nouvel audit complet avant remise en service |

## 5. Droits des candidats

- **Information.** La *Loi sur la protection des renseignements personnels dans le secteur privé* (art. 12.1, introduit par la Loi 25) prévoit qu'une personne soit informée d'une décision fondée exclusivement sur un traitement automatisé et puisse présenter ses observations à un membre du personnel en mesure de la réviser. Notre recommandation : aucune décision de refus n'est purement automatisée. Le candidat reçoit les **facteurs principaux** de son score (cote R, heures déclarées) et la marche à suivre pour contester.
- **Correction.** Le candidat peut faire corriger une donnée erronée, par exemple des heures de travail sous-déclarées. Le dossier est alors re-noté.
- **Contestation.** Le recours est traité par une personne qui n'a pas pris la décision initiale, dans un délai fixé et publié.

## 6. Cycle de vie et documentation

- **Fiche de modèle** à jour : données, définition du mérite, métriques par groupe, limites. Le carnet d'audit en est la première version.
- **Versionnage** du code, des coefficients et des seuils. Chaque décision est journalisée avec la version du modèle qui l'a produite.
- **Revue annuelle** par un comité d'éthique où siègent des représentants des régions éloignées et des étudiants. Le comité revalide la définition du mérite et les seuils.
- **Ré-estimation** au moins une fois par an, ou après toute alerte de niveau Arrêt. Chaque ré-estimation est suivie d'un audit complet (`audit_rapport.ipynb`) avant mise en service.
- **Rapport de transparence** annuel : taux d'octroi par région, écart d'égalité des chances mesuré sur l'échantillon de contrôle, nombre de contestations et de renversements.
- **Gestion du risque de modèle.** Le processus suit les pratiques usuelles des institutions financières : validation indépendante, inventaire des modèles, revue avant mise en production.

## 7. Risques résiduels assumés

1. **La définition du mérite est normative.** Garder les heures de travail et exclure le revenu sont des choix de valeur. Nous les défendons, mais ils doivent être entérinés par l'institution.
2. **Les heures de travail sont un proxy régional** (AUC 0,81) qui joue ici en faveur des régions éloignées. Si la population change, cet effet pourrait s'inverser. L'indicateur 5 le surveille.
3. **L'hypothèse structurelle.** La correction suppose une pénalité additive et uniforme. L'audit la soutient (aucune interaction, p = 0,58), mais un biais plus subtil chez de futurs décideurs humains ne serait pas capté. L'échantillon de contrôle est le filet de sécurité.
4. **Données synthétiques.** Toutes les conclusions doivent être revérifiées sur des données réelles avant tout déploiement.
