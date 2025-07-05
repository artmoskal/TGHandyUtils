# DATABASE SCHEMA CONSISTENCY PLAN

> **⚠️ IDENTIFIED CRITICAL ISSUE ⚠️**  
> Database schema has inconsistencies between migrations, repositories, and models.
> Need systematic cleanup to prevent runtime failures and data corruption.

## 🎯 **KEY PRINCIPLES FOR EFFECTIVE PLANNING**

### **Planning Philosophy:**
1. **Start with Diagnosis** - Audit current schema state thoroughly
2. **Test-First MANDATORY** - Write failing tests BEFORE making any changes
3. **Preserve Data** - Ensure no data loss during schema fixes
4. **Single Source of Truth** - Establish definitive schema reference
5. **Document Progress** - Real-time status tracking with clear completion criteria

---

## 📋 PROGRESS TRACKER

### 🚀 CURRENT STATUS
**Currently Working On:** NOT STARTED - PLANNING ONLY  
**Last Updated:** 2025-07-05  
**Next Priority:** Database schema audit and consistency plan

**✅ COMPLETED:** 
- ✅ Problem identification and scope definition
- ✅ Initial analysis of inconsistencies found

**🚧 IN PROGRESS:**
- Planning phase (this document)

**📋 PENDING:**
- Schema audit
- Consistency fixes
- Migration system cleanup
- Test coverage for schema

---

## 🔥 SECTION 1: CRITICAL - DATABASE TABLE NAME INCONSISTENCIES

### Overview
Database migration system references `unified_recipients` table while repository uses `recipients` table. This creates potential runtime failures and confusion about which table is the source of truth.

### 📊 Diagnostic Commands
```bash
# Check actual database schema
echo "=== Current Database Tables ==="
docker exec tghandyutils-bot-1 sqlite3 /app/data/tghandyutils.db ".tables"

echo "=== Recipients Table Schema ==="
docker exec tghandyutils-bot-1 sqlite3 /app/data/tghandyutils.db ".schema recipients"
docker exec tghandyutils-bot-1 sqlite3 /app/data/tghandyutils.db ".schema unified_recipients"

echo "=== Repository References ==="
grep -n "FROM.*recipients\|FROM.*unified_recipients" database/unified_recipient_repository.py
grep -n "unified_recipients\|recipients" database/migrations.py

echo "=== Model Field Names ==="
grep -n "title\|task_title\|description\|task_description" models/task.py
grep -n "title\|task_title\|description\|task_description" database/repositories.py
```

### Root Cause Analysis
```
# PROBLEM: Mixed table name references between unified_recipients and recipients
# IMPACT: Potential runtime failures, data fragmentation, confusion
# CAUSE: Incomplete migration from unified_recipients to recipients naming
```

### Detailed Fix Checklist
- [ ] **1.1 Diagnostic Phase**
  - [ ] Document current database state (which tables actually exist)
  - [ ] Identify all code references to both table names
  - [ ] Check if data exists in both tables or just one
  - [ ] Verify current repository functionality works
  
- [ ] **1.2 Decision Phase**  
  - [ ] Choose single table name (recipients recommended)
  - [ ] Plan migration strategy if both tables have data
  - [ ] Update all references to use consistent naming
  - [ ] Plan backward compatibility if needed
  
- [ ] **1.3 Implementation Phase**
  - [ ] Update migration system to use chosen table name
  - [ ] Update repository to use consistent table name
  - [ ] Create migration to consolidate data if needed
  - [ ] Update all documentation references
  
- [ ] **1.4 Testing Phase**
  - [ ] Write tests that verify table consistency
  - [ ] Test repository operations work correctly
  - [ ] Test migration system creates correct schema
  - [ ] Integration test with real database

### Success Criteria
- [ ] Single table name used consistently across all code
- [ ] Repository operations work without table name confusion
- [ ] Migration system creates correct schema
- [ ] No data loss during consolidation
- [ ] All tests pass with consistent schema

---

## 🔥 SECTION 2: CRITICAL - TASK FIELD NAME INCONSISTENCIES

### Overview
Database schema uses `title` and `description` fields while some code references `task_title` and `task_description`. This creates field mapping inconsistencies.

### 📊 Diagnostic Commands
```bash
echo "=== Task Table Schema ==="
docker exec tghandyutils-bot-1 sqlite3 /app/data/tghandyutils.db ".schema tasks"

echo "=== Field References in Code ==="
grep -n "task_title\|title" models/task.py database/repositories.py
grep -n "task_description\|description" models/task.py database/repositories.py

echo "=== INSERT/SELECT Statements ==="
grep -n "INSERT.*title\|SELECT.*title" database/repositories.py
```

### Root Cause Analysis
```
# PROBLEM: Inconsistent field naming between schema and code references
# IMPACT: Potential SQL errors, field mapping failures
# CAUSE: Migration from task_title/task_description to title/description incomplete
```

### Detailed Fix Checklist
- [ ] **2.1 Diagnostic Phase**
  - [ ] Verify actual database field names
  - [ ] Find all code references to old field names
  - [ ] Check if both field names exist in database
  - [ ] Test current repository operations
  
- [ ] **2.2 Implementation Phase**  
  - [ ] Standardize on title/description (current schema)
  - [ ] Update all code references to use correct field names
  - [ ] Remove any legacy field name references
  - [ ] Update model definitions to match schema
  
- [ ] **2.3 Testing Phase**
  - [ ] Test task creation with correct field names
  - [ ] Test task retrieval operations
  - [ ] Test all repository methods work correctly
  - [ ] Integration test with real database operations

### Success Criteria
- [ ] All code uses consistent field names (title/description)
- [ ] Task repository operations work correctly
- [ ] No SQL field name errors in logs
- [ ] Model definitions match database schema

---

## 🔥 SECTION 3: MEDIUM - SCREENSHOT FIELD CLEANUP

### Overview
Recent commit removed screenshot fields but some references may still exist in database schema or migration system. Need to verify complete cleanup.

### 📊 Diagnostic Commands
```bash
echo "=== Screenshot Field References ==="
grep -n "screenshot" database/connection.py database/migrations.py
grep -n "screenshot" models/task.py database/repositories.py

echo "=== Database Schema Check ==="
docker exec tghandyutils-bot-1 sqlite3 /app/data/tghandyutils.db ".schema tasks" | grep screenshot
```

### Root Cause Analysis
```
# PROBLEM: Potential leftover screenshot field references after removal
# IMPACT: Schema bloat, potential migration issues
# CAUSE: Incomplete cleanup during screenshot feature removal
```

### Detailed Fix Checklist
- [ ] **3.1 Diagnostic Phase**
  - [ ] Find all remaining screenshot field references
  - [ ] Check if database still has screenshot columns
  - [ ] Verify functionality works without screenshot fields
  
- [ ] **3.2 Cleanup Phase** (if needed)
  - [ ] Remove any remaining screenshot field references
  - [ ] Clean up database schema if columns still exist
  - [ ] Update migration system to not add screenshot fields

### Success Criteria
- [ ] No screenshot field references in code (unless intentionally re-added)
- [ ] Database schema clean of unused screenshot columns
- [ ] Migration system doesn't add deprecated fields

---

## 📊 IMPLEMENTATION PRIORITY

### **Phase 1: Immediate (When Ready)**
1. **Diagnostic Phase** - Complete schema audit
2. **Table Name Consistency** - Critical for repository operations

### **Phase 2: Next**  
1. **Field Name Consistency** - Fix title/description inconsistencies
2. **Screenshot Cleanup** - Remove any leftover references

### **Phase 3: Validation**
1. **Comprehensive Testing** - All database operations
2. **Migration Testing** - Fresh database creation
3. **Integration Testing** - Real-world usage scenarios

---

## 🛠️ DIAGNOSTIC WORKFLOW COMMANDS

### Schema Audit
```bash
# Complete database inspection
docker exec tghandyutils-bot-1 bash -c "cd /app && python -c \"
from database.connection import DatabaseManager
dm = DatabaseManager('data/tghandyutils.db')
with dm.get_connection() as conn:
    cursor = conn.execute('SELECT name FROM sqlite_master WHERE type=\'table\'')
    tables = cursor.fetchall()
    print('Tables:', [t[0] for t in tables])
    for table in tables:
        if table[0] in ['recipients', 'unified_recipients', 'tasks']:
            print(f'\\n{table[0]} schema:')
            cursor = conn.execute(f'PRAGMA table_info({table[0]})')
            cols = cursor.fetchall()
            for col in cols:
                print(f'  {col[1]} {col[2]}')
\""

# Repository consistency check
grep -r "FROM.*recipients\|INSERT.*recipients\|UPDATE.*recipients" database/
```

---

## 🎯 SUCCESS METRICS

### 1. **Schema Consistency:**
- [ ] Single table name used throughout codebase
- [ ] Consistent field names across all components
- [ ] Migration system creates correct schema
- [ ] No deprecated field references

### 2. **Functionality Maintained:**
- [ ] All repository operations work correctly
- [ ] Task creation/retrieval functions properly
- [ ] Recipient management fully functional
- [ ] No runtime SQL errors

### 3. **Code Quality:**
- [ ] Clean, consistent database layer
- [ ] Proper migration system
- [ ] Comprehensive test coverage for schema
- [ ] Documentation reflects actual schema

---

## 🔄 ROLLBACK PLAN

**If critical issues arise:**
1. **Immediate Actions:**
   ```bash
   # Restore previous database schema
   git checkout HEAD~1 -- database/
   docker-compose restart
   ```

2. **Data Protection:**
   - Backup database before any schema changes
   - Test changes on copy first
   - Have rollback migration ready

3. **Validation:**
   - Verify application still works after rollback
   - Check data integrity maintained
   - Plan alternative approach

---

**Plan Version:** 1.0  
**Based on:** PLANNING_TEMPLATE.md  
**Usage:** Systematic fix for database schema inconsistencies - TO BE EXECUTED LATER