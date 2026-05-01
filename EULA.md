# END USER LICENSE AGREEMENT — TELECOM-TOWER-POWER

> **Resumo executivo.** Este documento é um sumário comercial dos termos
> aplicáveis ao serviço hospedado TELECOM-TOWER-POWER. Não substitui o
> Master Services Agreement (MSA), o Order Form e o
> [DPA-LGPD.md](DPA-LGPD.md) assinados, que prevalecem em caso de conflito.

Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER").
All rights reserved.

> **Nota sobre o Licenciante.** Enquanto a constituição de pessoa jurídica
> está em curso, o Licenciante é **Daniel Azevedo Novais**, pessoa física,
> operando sob o nome empresarial "TELECOM-TOWER-POWER". Os contratos
> serão automaticamente cedidos à PJ após sua constituição, sem
> necessidade de aditivo, mediante mera notificação ao Licenciado.

## 1. Partes

- **Licenciante**: TELECOM-TOWER-POWER (titular dos direitos sobre o
  software, modelos, dados curados e marca).
- **Licenciado**: pessoa física ou jurídica que aceita este EULA, contrata
  um plano publicado ou celebra Order Form específico.

## 2. Objeto

Licença limitada, não exclusiva, intransferível e revogável para uso do
serviço hospedado (API, UI, dashboards, SDK e documentação) conforme o
plano contratado e os limites de uso (rate limit, quotas, número de
usuários, número de receivers, volume de relatórios, chamadas Bedrock).

## 3. Direitos concedidos

- Uso do serviço conforme quotas e limites do plano.
- Integração do SDK/cliente em aplicações próprias do Licenciado.
- Geração e uso interno de relatórios, análises e saídas do serviço para
  finalidades operacionais do Licenciado.

## 4. Restrições

O Licenciado **NÃO** poderá, sem autorização prévia e expressa do
Licenciante:

- Realizar engenharia reversa, descompilação, desmontagem, extração de
  modelos ou pesos, ou qualquer tentativa de reconstruir os algoritmos,
  parâmetros ou dados de treino.
- Utilizar saídas do serviço (incluindo respostas dos endpoints
  `/coverage/predict`, `/analyze`, `/plan_repeater`, `/bedrock/*`) para
  **treinar, ajustar (fine-tuning), destilar ou avaliar modelos
  concorrentes**.
- Revender, sublicenciar, hospedar para terceiros, oferecer como serviço
  (white-label) ou disponibilizar como SaaS competitivo, total ou
  parcialmente, sem aditivo comercial específico.
- Publicar **benchmarks comparativos** com produtos concorrentes sem
  consentimento prévio por escrito do Licenciante.
- Realizar testes de carga, stress, fuzzing ou penetration testing fora
  de janelas previamente acordadas; o Licenciante poderá bloquear chaves
  e IPs em caso de tráfego anômalo.
- Utilizar o serviço para fins ilícitos, atividade discriminatória, ou em
  violação de regulamentação setorial (ANATEL, ANPD).
- Remover, ocultar ou alterar avisos de copyright, marca ou de
  licenciamento contidos nas saídas, relatórios ou interfaces.

## 5. Propriedade intelectual

- Todo o código-fonte, modelos treinados, pipelines, documentação,
  schemas, datasets curados e marcas permanecem de propriedade exclusiva
  do Licenciante.
- Saídas geradas pelo serviço (PDFs, JSON, KML, predições) podem ser
  livremente utilizadas pelo Licenciado **dentro** dos limites deste
  EULA.
- Feedback fornecido pelo Licenciado (sugestões, correções, ideias)
  poderá ser incorporado ao serviço sem obrigação de remuneração.

## 6. Dados, modelos e LGPD

- O uso de dados pessoais é regido pelo [DPA-LGPD.md](DPA-LGPD.md).
- Modelos treinados, geocode caches e bases derivadas são regidos por
  [LICENSE-DATA.md](LICENSE-DATA.md).
- O Licenciado é responsável pela base legal (Art. 7º LGPD) do
  tratamento de dados que insere no serviço.

## 7. Disponibilidade e SLA

- Serviço fornecido **"no estado em que se encontra"**, salvo SLA
  específico previsto no plano Enterprise ou Order Form.
- Janelas de manutenção programadas serão comunicadas com antecedência
  razoável.
- Créditos de SLA, quando aplicáveis, são o **único e exclusivo** remédio
  por indisponibilidade.

## 8. Garantias e limitação de responsabilidade

- O Licenciante **não garante** adequação a finalidade específica,
  ausência de erros, conformidade regulatória de decisões tomadas com
  base nas saídas, nem aprovação automática perante ANATEL.
- Decisões de engenharia de RF, aquisição de sites, dimensionamento de
  enlaces e protocolização de licenças junto à ANATEL são **de
  responsabilidade exclusiva** do Licenciado.
- A responsabilidade total agregada do Licenciante por qualquer
  reivindicação, em qualquer base legal, está limitada ao valor pago
  pelo Licenciado nos **12 (doze) meses anteriores** ao evento gerador.
- Em nenhuma hipótese o Licenciante responderá por lucros cessantes,
  perda de oportunidade, danos indiretos, incidentais ou consequenciais.

## 9. Pagamento e suspensão

- Falha de pagamento por mais de 15 dias autoriza suspensão do serviço.
- Falha por mais de 60 dias autoriza rescisão e exclusão de dados,
  observado o prazo de retenção do [DPA-LGPD.md](DPA-LGPD.md).

## 10. Vigência e rescisão

- Este EULA vigora enquanto houver plano ativo ou Order Form em vigor.
- Violação material das Seções 4, 5 ou 6 autoriza **rescisão imediata** e
  medidas injuntivas (cautelares), independentemente de notificação
  prévia.
- Em caso de rescisão, o Licenciado deverá cessar imediatamente o uso e
  poderá solicitar exportação de seus dados em até 30 dias.

## 11. Confidencialidade

- Informações técnicas, comerciais, preços negociados, métricas de
  desempenho, arquitetura interna e detalhes do modelo são
  **Informações Confidenciais** e ficam sujeitas a obrigação de sigilo
  por 5 anos após o término.

## 12. Marca e atribuição

- O Licenciado pode mencionar o uso do serviço para fins comerciais
  legítimos.
- Uso do logotipo, identidade visual ou referência como "case" requer
  autorização escrita.

## 13. Lei aplicável e foro

- Este EULA é regido pelas leis da República Federativa do Brasil.
- Fica eleito o foro da Comarca de **Brasília/DF** para dirimir
  controvérsias, salvo acordo em contrário em Order Form específico ou
  exigência de consumidor pessoa física conforme CDC.

## 14. Aviso legal

Este resumo executivo **não substitui** o License Agreement, MSA, DPA e
SLA assinados. Em caso de divergência, prevalecem os instrumentos
contratuais formais.
