# Data Input and Output Description for the AI-Agent Pre Re-routing Model

_Document prepared by Ericka for review.  Items marked_ **_TBC_** _(to be confirmed) are not yet defined, and they will be defined after User Workflow compliance.

## 1. Purpose and scope

This report describes the data each stage of the pre re-routing pipeline receives and produces, and how data passes between stages. The pipeline has five stages: data acquisition and feature engineering, predictive modelling for maritime chokepoint blockage, data structure, the re-routing signal to the customer, and spatial simulation of the new route.

The core concept: an AI agent predicts whether a maritime chokepoint will be blocked, based on media information about an event, the vessel's geography and the congestion state of the port the vessel is heading to. If a blockage is predicted, the agent sends the customer a re-routing signal and lets them choose among the available alternative routes. The hypothesis is that sentiment analysis of events around a chokepoint provides factors for calculating the probability of a blockage.

## 2. Reading guide

Every stage section follows the same template: purpose, inputs, outputs and limits.

## 3. Data Pipeline Stages

| From                    | To             | Data object                                                                 | Source                                                              |
| ----------------------- | -------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Stage 1: Acquisition    | Stages 2 and 3 | News events, sentiment scores, geography, port congestion, labelled targets | GDELT API, IMF PortWatch via HDX                                    |
| Stage 2: Modelling      | Stage 4        | Blocked or not-blocked classification                                       | Classification model output                                         |
| Stage 3: Data structure | Stage 2        | Aggregated time series dataframe                                            | Dataframe (date, chokepoint, news risk score, vessel count, target) |
| Stage 4: Signal         | Customer       | Re-routing email with route options and probabilities                       | Gmail API                                                           |
| Stage 5: Simulation     | Customer       | Spatial simulation of the new route                                         | World Labs spatial generator                                        |

## Stage 1: Data acquisition and feature engineering

**Purpose**: Collect and prepare the raw data, features and labels the model needs.

**Inputs**

- Raw news data around maritime chokepoints, requested from the GDELT API (which eases web scraping).
- The Goldstein scale, taken from GDELT, as the sentiment measure.
- Geographical data: the current vessel distance from the event and from the chokepoint.
- Current port congestion of the destination port, measured as Vessel Count.
- Labelled target data on when blockages actually happened, from IMF PortWatch accessed through the Humanitarian Data Exchange (HDX), focused on the Middle East region to keep the model niche.
- Headlines and article text for each chokepoint event, read by an open vLLM running on AMD Developer Cloud.

**Outputs**

- A 0-10 News Risk Score with a short reason, produced by the vLLM. It can sit alongside the GDELT Goldstein score or be compared with it.
- Engineered features: Goldstein score, distances, vessel count.
- Labelled targets teaching the model what a blockage is.

**Limits.** Update frequency and API limits: **TBC.**

## Stage 2: Predictive modelling for maritime chokepoint blockage

**Purpose.** Classify whether a chokepoint will be blocked, to decide whether to re-route.

**Inputs**

- The aggregated time series dataframe from Stage 3, with its labelled targets.

**Outputs**

- A classification: blocked or not blocked, used for the re-routing decision.
- Models in scope: logistic regression or XGBoost as simpler models; time series models such as LSTM (PyTorch) as more complex models.
- AMD application: PyTorch ROCm builds, training the LSTM on the AMD GPU.

**Limits.** The probability threshold for triggering a re-routing signal, and evaluation metrics: **TBC.**

## Stage 3: Data structure

**Purpose.** Hold the data as an aggregated time series for modelling.

**Inputs**

- News Risk Score, vessel count and target labels from Stage 1, by date and chokepoint.

**Outputs**

An aggregated time series dataframe with these fields:

| column | Field           | Type             | Description                                     |
| ------ | --------------- | ---------------- | ----------------------------------------------- |
| 0      | date            | timestamp        | Index                                           |
| 1      | chokepoint      | string           | Geolocation data                                |
| 2      | news risk score | numerical, float | Score 0-10                                      |
| 3      | vessel count    | numerical, int   | Number of vessels in the port (port congestion) |
| 4      | target          | binary           | 1 = yes (disrupted), 0 = no                     |

**Limits.** Time granularity (daily or other)**TBC.**

## Stage 4: Re-routing signal to the customer

**Purpose**: Tell the customer a blockage is predicted and let them choose a new route.

**Inputs**

- The blocked or not-blocked prediction from Stage 2, stored in the target of stage 3 dataframe.
- The available alternative routes and the probability for each and how these are computed. **TBC.**

**Outputs**

- An email signal, sent through the Gmail API, showing that a disruption (blockage) is predicted.
- Multiple choices, each with the probability for the available new route, from which the customer chooses.

**Limits.** Format of the customer's reply and how the chosen route is passed to Stage 5: **TBC.**

## Stage 5: Spatial simulation of the new route

**Purpose:** Show the customer the new route and compare scenarios.

**Inputs**

- The new route options or the customer's chosen route.

**Outputs**

- A spatial simulation of the new route, using World Labs' fast spatial generator.
- Scenario and contingency spatial analysis (erouted terminal flows), complementing graph-based network analysis.
- AMD application: accelerating the World Labs simulation on AMD Developer Cloud.

**Limits.** Source data for the route geometry and the delivery format of the simulation to the customer: **TBC.**

## 4. Data governance and quality

Data ownership, licensing of GDELT, PortWatch and HDX data, label quality and class balance of blockage events, and customer data handling for emails: all TBC.

## 11. Limits and open items

- The hypothesis that event sentiment is a factor for the blockage probability is yet to be validated.
- Labelled targets focus on the Middle East region.
- Items marked TBC above need decisions or specifications.