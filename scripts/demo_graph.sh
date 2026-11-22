#!/bin/bash
# Demo script showing all graph commands against samples/demo_repo

set -e

echo "========================================="
echo "CodeCompass Graph Commands Demo"
echo "========================================="
echo ""

# Ensure we're in the project root
cd "$(dirname "$0")/.."

# First, index the demo repo if not already indexed
echo "--- Indexing demo_repo ---"
python -m codecompass.cli index samples/demo_repo --name demo_repo --full --no-progress
echo ""

# Find the create_order_endpoint symbol ID
echo "--- Finding create_order_endpoint symbol ---"
python -c "
from codecompass.db import init_db, close_db
from codecompass.db.search import find_definition_ranked
init_db()
results = find_definition_ranked('api.create_order_endpoint', kind_filter='function', limit_count=1)
if results:
    print(f'SYMBOL_ID={results[0][\"symbol_id\"]}')
close_db()
" > /tmp/symbol_id.txt

SYMBOL_ID=$(cat /tmp/symbol_id.txt | grep SYMBOL_ID | cut -d= -f2)
echo "Found symbol ID: $SYMBOL_ID"
echo ""

# Test all graph commands
echo "========================================="
echo "1. Direct callers (depth=1)"
echo "========================================="
python -m codecompass.cli callers $SYMBOL_ID --depth 1 --min-confidence 0.0
echo ""

echo "========================================="
echo "2. Transitive callers (depth=10)"
echo "========================================="
python -m codecompass.cli callers $SYMBOL_ID --depth 10 --min-confidence 0.0
echo ""

echo "========================================="
echo "3. Direct callees (depth=1)"
echo "========================================="
python -m codecompass.cli callees $SYMBOL_ID --depth 1 --min-confidence 0.0
echo ""

echo "========================================="
echo "4. Transitive callees (depth=10)"
echo "========================================="
python -m codecompass.cli callees $SYMBOL_ID --depth 10 --min-confidence 0.0
echo ""

echo "========================================="
echo "5. Transitive impact (callers + callees)"
echo "========================================="
python -m codecompass.cli impact $SYMBOL_ID --depth 10 --min-confidence 0.0
echo ""

echo "========================================="
echo "6. References to symbol"
echo "========================================="
python -m codecompass.cli refs $SYMBOL_ID --min-confidence 0.0
echo ""

echo "========================================="
echo "7. Symbol context (radius=2)"
echo "========================================="
python -m codecompass.cli context $SYMBOL_ID --radius 2
echo ""

# Find BaseHandler for implementations demo
echo "========================================="
echo "8. Find BaseHandler implementations"
echo "========================================="
python -m codecompass.cli impls "BaseHandler" --repo demo_repo
echo ""

# Find UserHandler for implementations demo
echo "========================================="
echo "9. Find UserHandler implementations"
echo "========================================="
python -m codecompass.cli impls "UserHandler" --repo demo_repo
echo ""

# Test with min_confidence filter
echo "========================================="
echo "10. Callers with min_confidence=0.7"
echo "========================================="
python -m codecompass.cli callers $SYMBOL_ID --depth 10 --min-confidence 0.7
echo ""

echo "========================================="
echo "11. Find definition (ranked)"
echo "========================================="
python -m codecompass.cli find "create_order_endpoint" --repo demo_repo --kind function
echo ""

echo "========================================="
echo "12. Find definition (exact only)"
echo "========================================="
python -m codecompass.cli find "create_order_endpoint" --repo demo_repo --kind function --exact
echo ""

echo "========================================="
echo "Demo complete!"
echo "========================================="