Output docs/demo.gif

Set FontSize 15
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Dracula"
Set Shell "bash"
Set TypingSpeed 30ms

# Prepare the exact public source revision; setup output stays out of the recording.
Hide
Type "python docs/demo/setup.py && cd /tmp/ariadne-demo-rec && clear" Enter
Show
Sleep 1s

Type "# Question from an AI coding harness:" Enter
Type "# Which services and APIs participate in the 'owner' flow?" Enter
Sleep 5s

Type "# Ariadne reads contracts with deterministic local static analysis." Enter
Type "# No model call, embeddings, vector database, or network query." Enter
Sleep 5s

Type "# Five pinned Spring Petclinic services:" Enter
Type "cat ariadne.config.json" Enter
Sleep 8s

Type "ariadne-mcp --db petclinic.db scan --config ariadne.config.json" Enter
Sleep 10s

Type "# Query time is the local SQLite + token scoring path:" Enter
Type "/usr/bin/time -p ariadne-mcp --db petclinic.db query owner" Enter
Sleep 15s

Type "# Result: gateway -> customers service, plus the visit API fan-out." Enter
Type "# Reproduce: python examples/run.py spring-petclinic" Enter
Sleep 8s
