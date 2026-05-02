# Validação do modelo de cobertura

Esta página documenta a metodologia, os números atuais e o pipeline de
calibração do modelo de predição de cobertura RF do TELECOM TOWER POWER
(arquivo `coverage_model.npz`, versão `ridge-v1`).

> **Para procurement / due-diligence Enterprise:** todos os números
> abaixo são reproduzíveis a partir do código-fonte público
> (`coverage_predict.py`, `scripts/retrain_coverage_model.py`) e a partir
> do endpoint autenticado `GET /coverage/model/info`. Não há "magia
> proprietária" — o modelo é uma regressão ridge sobre features físicas
> e de terreno, treinada diariamente.

---

## 1. Arquitetura de modelo

| Camada | Tecnologia | Função |
|---|---|---|
| Local | Ridge regression (NumPy) | Inferência sub-ms; usado em 100 % das requests |
| Opcional | SageMaker endpoint (XGBoost / NN) | Override quando `SAGEMAKER_COVERAGE_ENDPOINT` está setado |
| Opcional | Bedrock (Claude) | Narrativa em linguagem natural sobre o resultado numérico |
| Fallback | Free-space + Fresnel determinístico | Garante que `/analyze` nunca retorna 500 |

A regressão ridge usa **17 features** engineered: distância,
frequência, alturas TX/RX, potência, ganho, e estatísticas de terreno
SRTM (média, máximo, desvio-padrão, slope, número de obstruções,
obstrução máxima, razão Fresnel mínima) + 3 termos não-lineares
(`log_d²`, `log_d·log_f`, `terrain_std·log_d`).

---

## 2. Metodologia de avaliação

A cada execução do pipeline `retrain-coverage-model.yml` (cron diário
06:15 UTC) o modelo é avaliado com **k-fold cross-validation
(k=5 por padrão)** sobre o dataset sintético + observações reais.

### O que é reportado

| Métrica | Significado | Onde aparece |
|---|---|---|
| `rmse_db` | RMSE in-sample (treino) | Histórico — útil só para detectar regressão |
| `cv_rmse_db` | **RMSE médio em holdout out-of-fold** | Métrica primária; é o número que importa |
| `cv_rmse_std_db` | Desvio-padrão entre folds | Mede instabilidade do modelo |
| `cv_folds` | k usado | Default: 5 |
| `rmse_by_morphology` | RMSE bucketizado por morfologia (open/rolling/mountainous) | Caudas por terreno |
| `rmse_by_band` | RMSE por banda comercial (700/850/900/1800/2100/2600/3500 MHz) | Caudas por frequência |

### Como o holdout é construído

1. Permutação determinística (seed=42) do dataset.
2. Split em k folds iguais.
3. Para cada fold: treina nos demais (k-1), prediz no holdout.
4. RMSE do fold = RMSE entre predição e label do holdout.
5. RMSE por morfologia / banda usa as predições out-of-fold concatenadas
   — cada amostra tem exatamente uma predição feita por um modelo que
   **não a viu durante o treino**.

Código: [`coverage_predict._kfold_evaluate`](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/coverage_predict.py).

### Buckets de morfologia

| Label | Critério (`terrain_std_m`) | Equivalente operacional |
|---|---|---|
| `open_or_flat` | < 5 m | Cerrado / pampas / planícies costeiras |
| `rural_rolling` | 5–15 m | Mata Atlântica de baixa altitude, agreste |
| `rural_mountainous` | ≥ 15 m | Serra do Mar, Espinhaço, Amazônia ondulada |

Buckets escolhidos para alinhar com a forma como engenheiros de campo
brasileiros descrevem terreno — não com a taxonomia ITU-R P.1812
(que será adicionada em paralelo no roadmap MapBiomas).

---

## 3. Como ler os números atuais

```bash
curl -fsS https://api.telecomtowerpower.com.br/coverage/model/info \
  -H "X-API-Key: <sua chave>" | jq .
```

Resposta de exemplo:

```json
{
  "local_model": {
    "version": "ridge-v1",
    "rmse_db": 12.83,
    "cv_rmse_db": 14.91,
    "cv_rmse_std_db": 1.87,
    "cv_folds": 5,
    "n_train": 11843,
    "trained_at": 1746128462.0,
    "rmse_by_morphology": {
      "open_or_flat": 9.42,
      "rural_rolling": 12.05,
      "rural_mountainous": 18.31
    },
    "rmse_by_band": {
      "700MHz":  8.21,
      "850MHz": 10.13,
      "900MHz": 11.47,
      "1800MHz": 13.82,
      "2100MHz": 15.06,
      "2600MHz": 16.94,
      "3500MHz": 18.55
    }
  }
}
```

Os mesmos números são exportados como métricas Prometheus em
`/metrics`:

```
coverage_model_rmse_db                       # in-sample
coverage_model_cv_rmse_db                    # holdout (mean)
coverage_model_cv_rmse_std_db                # holdout (std)
coverage_model_cv_folds                      # k
coverage_model_rmse_by_morphology_db{morphology="..."}
coverage_model_rmse_by_band_db{band="..."}
coverage_observations_total{source="..."}    # cumulative ground-truth
coverage_observation_residual_db_bucket{...} # live residual histogram
```

Painéis Grafana ficam em `monitoring.telecomtowerpower.com.br`
(acesso restrito a operadores).

---

## 4. Targets de RMSE por persona de cliente

Estes targets refletem a precisão necessária por tipo de operador. Não
é compromisso de SLA — é referência para procurement avaliar fit.

| Cliente alvo | RMSE holdout aceitável | TTP entrega hoje? |
|---|---|---|
| WISP rural / regional ISP | ≤ 15 dB | ✅ |
| MVNO / regional CLEC | ≤ 10 dB | ⚠️ apenas em bandas baixas (700/850 MHz) |
| Tier-2 (Algar, Sercomtel, Brisanet) | ≤ 8 dB | ❌ não — roadmap MapBiomas + ITU-R P.1812 |
| Tier-1 (Vivo, Claro, TIM) | ≤ 6 dB + drive-test loop validado | ❌ não — exige modelagem física híbrida + frota de drive-test |

O posicionamento atual do produto é WISP/regional-ISP rural. Tier-2
está no roadmap explícito ([notes/tier1-roadmap.md](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/notes/tier1-roadmap.md));
Tier-1 não é alvo nos próximos 18 meses.

---

## 5. Loop de calibração com dados reais

### Como o modelo aprende com o seu uso

1. Engenheiro do tenant faz uma predição via `POST /analyze` ou `POST /coverage/predict`.
2. Em campo, ele mede o RSSI real e submete via:
   - `POST /coverage/observations` (uma medida)
   - `POST /coverage/observations/batch` (CSV bulk — drive-test)
3. A medida vai para a tabela `link_observations` (Postgres, encrypted at rest).
4. O job `retrain-coverage-model.yml` (cron diário) verifica se houve
   ≥ 1.000 novas observações desde o último retrain.
5. Se sim: treina ridge novo blendando sintético + observações reais
   (peso 3× nas reais), avalia via k-fold, sobe `.npz` para S3, força
   redeploy ECS para hot-reload.

### O que ainda **não** está automatizado (gap reconhecido)

- ✅ **Importer de drive-test TEMS / G-NetTrack / QualiPoc** — disponível
  em `POST /coverage/observations/drivetest`. Aceita CSV com
  auto-detecção de cabeçalhos (`Latitude`/`lat`, `RSRP`/`signal_dbm`/`RxLev`,
  `Frequency [MHz]`/`band`, etc.). Persiste em `link_observations`
  com `source='drive_test'`.
- ✅ **Calibração por banda separada** — `coverage_predict.train_band_aware_model()`
  treina um ridge dedicado para cada uma das sete bandas comerciais brasileiras
  (700 / 850 / 900 / 1800 / 2100 / 2600 / 3500 MHz) mais um modelo global de
  fallback. Persistido como `coverage_model_<MHz>.npz` em um diretório apontado
  por `COVERAGE_BAND_MODEL_DIR`. CLI: `python -m coverage_predict train-bands
  --out-dir band_models`. Quando ativo, `predict_signal` snap-a `f_hz` ao band
  mais próximo (e.g. 1.95 GHz → 1800, 3.6 GHz → 3500), aplicando coeficientes
  específicos. RMSE típica reduz ~30 % em PoCs e a confiança reportada via
  `/coverage/predict` deixa de ser uma média ponderada entre regimes
  fundamentalmente diferentes.
- ✅ **Features de clutter (vegetação, urbanização)** — sampler de
  uso/cobertura do solo (MapBiomas Coleção 9, single-band uint8) com
  cache Redis (TTL 30 d) + LRU(8192) em processo, mirror de
  `MAPBIOMAS_RASTER_S3_URI` para `MAPBIOMAS_RASTER_PATH` no boot do
  container (~1 GB Brasil-wide, download em ~7 s). `/coverage/predict`
  (point mode) já retorna `clutter_class` + `clutter_label` quando
  `rx_lat`/`rx_lon` são fornecidos. O pipeline `build_features(...,
  with_clutter=True)` anexa um one-hot de 10 dims (Forest / Savanna /
  Grassland / Pasture / Mosaic / Urban / Bare / Water / Soybean /
  Other) ao vetor v1; o schema é versionado em
  `CoverageModel.feature_names` (persistido no `.npz`), portanto
  artefatos antigos (17 dims) continuam carregando — e modelos
  futuros treinados com clutter (27 dims) são detectados em runtime
  via `len(feature_names)`.
- ⚠️ **Retrain com clutter** — pendente: o flag `--with-clutter` no
  `scripts/retrain_coverage_model.py` só faz sentido depois que
  observações de drive-test com `rx_lat`/`rx_lon` se acumularem (dados
  sintéticos não têm sinal de clutter para aprender).

Próximo gap aberto: ITU-R P.1812 como blender físico para baixa
contagem de amostras (Tier-2 target).

---

## 6. Reprodutibilidade

Para reproduzir os números reportados em uma máquina local:

```bash
git clone https://github.com/danielnovais-tech/TELECOM-TOWER-POWER
cd TELECOM-TOWER-POWER
pip install numpy
python3 -c "
import coverage_predict as cp
m = cp.train_model(n_synthetic=10_000, kfold=5, seed=42)
print(f'rmse        = {m.rmse_db:.2f} dB')
print(f'cv_rmse     = {m.cv_rmse_db:.2f} \u00b1 {m.cv_rmse_std_db:.2f} dB (k={m.cv_folds})')
print('per-band:', m.rmse_by_band)
print('per-morph:', m.rmse_by_morphology)
"
```

A seed é fixa (42), portanto duas execuções da mesma versão de código
devem produzir exatamente os mesmos números.

---

## 7. Histórico

O job `retrain-coverage-model.yml` grava um **marker JSON** ao lado
do `.npz` no S3 (`s3://telecom-tower-power-results/models/coverage_model.last_retrain.json`)
com todos os campos acima a cada execução. Auditores Enterprise podem
solicitar acesso somente-leitura via [contact.md](../contact.md) para
verificar a evolução temporal dos números.
