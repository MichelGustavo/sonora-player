# SONORA — Music Library

Player de música desktop em Python, com interface escura e minimalista.

## Recursos

- Seleção de uma pasta e leitura recursiva de arquivos de áudio.
- Biblioteca de faixas e seção exclusiva de álbuns.
- Agrupamento por **artista do álbum + álbum**, usando metadados.
- Capa incorporada ao arquivo exibida no player e nos cartões de álbum.
- Reprodução, pausa, faixa anterior/próxima, busca na biblioteca, barra de progresso e volume.
- Modos de repetição: desligado, repetir fila e repetir faixa atual.
- Editor de metadados: título, artista, álbum, artista do álbum, ano/data, número da faixa e gênero.
- Opção de incorporar uma nova capa ao arquivo (formatos compatíveis).
- Atalhos: `Ctrl+O` abre pasta, `Ctrl+E` edita a faixa selecionada e `Espaço` reproduz/pausa.
- Última pasta selecionada salva nas configurações do aplicativo.

## Instalação

Requer Python 3.10 ou superior.

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

### macOS / Linux

Para iniciar pelo script, ele cria o ambiente virtual local e instala as dependências
de `requirements.txt` se ainda estiverem faltando:

```bash
./run_linux.sh
```

Ou configure o ambiente manualmente:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Formatos

A biblioteca reconhece arquivos com extensão MP3, FLAC, WAV, M4A, AAC, OGG, OPUS, WMA, AIFF e MP4. A reprodução depende dos codecs disponíveis no backend multimídia do Qt e do sistema operacional; por isso, a disponibilidade efetiva pode variar entre Windows, macOS e Linux.

A leitura/gravação de tags usa Mutagen. A edição da capa incorporada é implementada para MP3/WAV/AIFF, FLAC, M4A/MP4 e OGG/Opus. Alguns arquivos com proteção, tags incomuns ou permissões restritas podem impedir a gravação.

## Observações

- O SONORA altera os metadados diretamente nos arquivos de áudio. É recomendável manter um backup da coleção antes de editar tags em lote.
- O aplicativo não envia músicas para a internet; a biblioteca é lida localmente.
- A pasta é percorrida incluindo suas subpastas.
