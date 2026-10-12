from classBSLAnalyzer import BSLAnalyzer

def main():
    analyzer = BSLAnalyzer()
    cond = analyzer.interactive()
    while cond:
        cond = analyzer.interactive()

if __name__ == "__main__":
    main()
