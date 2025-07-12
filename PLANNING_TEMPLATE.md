# Development Planning Template

> **⚠️ IMPORTANT: DO NOT DELETE THIS TEMPLATE ⚠️**  
> This template has proven successful for complex refactoring projects.  
> It should be preserved as a reference for all future development planning.  
> Last used successfully: June 2025 for monolithic handler refactoring

## 🎯 **KEY PRINCIPLES FOR EFFECTIVE PLANNING**

### **🚨 MANDATORY ANALYSIS BEFORE ANY TASK 🚨**
**BEFORE starting ANY implementation, ALWAYS:**
1. **Read existing test files** - Understand current testing patterns and infrastructure
2. **Read existing MD files** - Follow established approaches and avoid rework  
3. **Check for Factory Boy usage** - Never revert to mocks if Factory Boy is available
4. **Verify architectural decisions** - Follow established DI, service, and testing patterns
5. **Document analysis findings** - Record what patterns/tools are already in use
6. **Search for similar code** - `grep -r "ClassName" --include="*.py" .`
7. **Check git history** - `git log -S"feature_name" --source --all`
8. **List affected entities** - Models, services, handlers that will be touched

### **Planning Philosophy:**
1. **Start with Diagnosis** - Always audit current state before making changes
2. **Prioritize by Impact** - Fix foundational issues first (FSM, DI, core architecture)
3. **Test-First MANDATORY** - Write failing tests BEFORE making any changes
4. **Test Everything** - Minimum 60% test coverage for modified code
5. **Document Progress** - Real-time status tracking with clear completion criteria
6. **Plan for Rollback** - Always have a way back if things go wrong
7. **NEVER REVERT ARCHITECTURE** - Don't undo improvements (e.g., Factory Boy → mocks)
8. **Clean Git History** - No commented code, squash commits, clear messages

### **Test-First Development Protocol:**
**CRITICAL**: NEVER implement changes without failing tests first
1. **Write Failing Test** - Reproduce the problem or test the new feature
2. **Verify Test Fails** - Confirm test catches the issue
3. **Implement Fix** - Make minimal changes to pass the test
4. **Verify Test Passes** - Confirm the fix works
5. **Regression Check** - Run full test suite
6. **Refactor & Document** - Clean code, add docstrings, update docs

### **Quality Gates:**
- ✅ **Comprehensive Logging** - Debug, info, warning, error at appropriate levels
- ✅ **Error Handling** - No silent failures, graceful degradation
- ✅ **Dependency Injection** - Use DI container, never direct instantiation
- ✅ **SOLID Principles** - Single responsibility, proper abstractions
- ✅ **Test Coverage** - Minimum 60% for modified code
- ✅ **Type Hints** - All functions must have proper type annotations
- ✅ **Docstrings** - All public methods documented
- ✅ **No Duplication** - Check for existing implementations first
- ✅ **Clean Code** - No TODOs, debug prints, or commented code

### **Planning Structure:**
- **CRITICAL** sections first (foundational systems)
- **HIGH** priority (main features)
- **MEDIUM** priority (enhancements)
- **LOW** priority (cleanup, optimization)

---

## 📋 PROGRESS TRACKER TEMPLATE

### 🚀 CURRENT STATUS
**Currently Working On:** [Section Name]  
**Last Updated:** [Date]  
**Next Priority:** [Specific Task]

**✅ COMPLETED:** 
- [List completed sections with brief status]

**🚧 IN PROGRESS:**
- [Current work items]

**📋 PENDING:**
- [Upcoming work items]

---

## 🔥 SECTION TEMPLATE STRUCTURE

### **SECTION X: [PRIORITY LEVEL] - [SECTION NAME]**

#### Overview
[Brief description of what this section addresses and why it's important]

#### 📊 Diagnostic Commands
```bash
# Check current state
echo "=== Analyzing existing code ==="
grep -r "FeatureName" --include="*.py" src/ handlers/ services/
find . -name "*.py" -exec grep -l "similar_function" {} \;

# Check test coverage before changes
docker-compose -f infra/docker-compose.test.yml run --rm bot-test \
  python -m pytest tests/ --cov=module_name --cov-report=term-missing

# Review git history
git log --oneline --grep="feature" -- path/to/files
```

#### Root Cause Analysis
```
# PROBLEM: [Description of the issue]
# IMPACT: [What breaks when this doesn't work]
# CAUSE: [Why this happened]
# EXISTING CODE: [Similar implementations found]
# CAN REUSE: [Components to reuse instead of creating new]
```

#### Detailed Fix Checklist
- [ ] **X.1 Pre-Implementation Analysis**
  - [ ] Search for duplicate functionality
  - [ ] Document existing patterns found
  - [ ] List all files that will be modified
  - [ ] Identify reusable components
  - [ ] Check requirements clarity (re-read 3x)
  
- [ ] **X.2 Test Writing Phase**
  - [ ] Write failing unit tests (happy path)
  - [ ] Write failing edge case tests
  - [ ] Use Factory Boy (not mocks!)
  - [ ] Verify tests fail correctly
  - [ ] Add integration tests if needed
  
- [ ] **X.3 Implementation Phase**  
  - [ ] Follow existing patterns (DI, services)
  - [ ] Add multi-level logging
  - [ ] Handle all error cases
  - [ ] Add type hints everywhere
  - [ ] No hardcoded values
  
- [ ] **X.4 Testing & Validation**
  - [ ] All tests passing (60%+ coverage)
  - [ ] No regressions in existing tests
  - [ ] Manual testing completed
  - [ ] Performance acceptable
  - [ ] Rollback tested
  
- [ ] **X.5 Cleanup & Review**
  - [ ] Remove ALL debug prints
  - [ ] Remove ALL commented code
  - [ ] Update documentation
  - [ ] Clean git history (squash)
  - [ ] No TODOs left

#### Implementation Example
```python
# Follow project patterns - check existing code for:
# - Service structure with DI
# - Repository patterns
# - Error handling approach
# - Logging standards
# - Test structure with Factory Boy

# Example will be project-specific based on codebase analysis
```

#### Success Criteria
- [ ] All tests passing
- [ ] Coverage >= 60% for new code
- [ ] No duplicate code/entities created
- [ ] Follows architectural patterns
- [ ] Clean git history
- [ ] Documentation updated
- [ ] No regressions

---

## 🛠️ DIAGNOSTIC COMMANDS REFERENCE

### Check for Duplicates
```bash
# Search for similar classes/functions
grep -r "class.*Similar" --include="*.py" .
grep -r "def.*function_name" --include="*.py" .

# Check imports to see what's already used
grep -r "from.*import" --include="*.py" . | grep -i "feature"
```

### Validate Architecture
```bash
# Check test structure
find tests/ -name "*.py" | head -20

# Verify Factory Boy usage
grep -r "Factory" tests/ --include="*.py"

# Check for mocks (should be minimal)
grep -r "Mock\|patch" tests/ --include="*.py"
```

### Git Archaeology  
```bash
# Who worked on this before
git blame path/to/file.py

# Find deleted code
git log --diff-filter=D --summary

# Search history for patterns
git log -S"ClassName" --all
```

---

## 🎯 SUCCESS METRICS

### Functionality & Quality
- [ ] Features work as specified
- [ ] Test coverage >= 60% (new), >= 40% (overall)
- [ ] No critical/high bugs
- [ ] Performance within baseline
- [ ] No duplicate implementations

### Architecture & Process
- [ ] SOLID principles followed
- [ ] DI properly used
- [ ] Clean git history
- [ ] All reviews passed
- [ ] Documentation complete

---

## 🔄 ROLLBACK PLAN

```bash
# Quick rollback if needed
git checkout main
git branch -D feature-branch

# Or selective rollback
git checkout [last-good-commit] -- [specific-files]

# Verify clean state
docker-compose -f infra/docker-compose.test.yml run --rm bot-test python -m pytest
```

---

**Template Version:** 1.2 (Concise)  
**Based on:** VERSION_FIXING.md (successful example from June 2025)  
**Remember:** Adapt commands to your project's actual structure