Output demo.gif

Set FontSize 15
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Dracula"
Set Shell "bash"
Set TypingSpeed 30ms

# Reset any leftover state
Hide
Type "cd /tmp/ariadne-demo-rec && rm -f petclinic.db && export PATH=$PWD:$PATH && clear" Enter
Show
Sleep 500ms

Type "# 5 Spring microservices. What touches 'owner'?" Enter
Sleep 800ms
Type "cat ariadne.config.json" Enter
Sleep 2s

Type "ariadne --db petclinic.db scan --config ariadne.config.json" Enter
Sleep 4s

Type "ariadne --db petclinic.db query owner" Enter
Sleep 4s

Type "# Two clusters. ~500 tokens. No embeddings, no network." Enter
Sleep 2500ms
