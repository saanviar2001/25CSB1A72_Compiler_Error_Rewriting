import java.util.ArrayList;
import java.util.List;

public class Grades {
    public static void main(String[] args) {
        List<Integer> scores = new ArrayList<>();
        scores.add(85);
        scores.add(92);
        scores.add(78)
        scores.add(88);

        int total = 0;
        for (int s : scores) {
            total += s;
        }

        double avg = (double) total / scores.size();
        System.out.println("Average: " + avg);
        System.out.println("Grade: " + getGrade(avg));
    }

    static char getGrade(double avg) {
        if (avg >= 90) return 'A';
        if (avg >= 80) return 'B';
        if (avg >= 70) return 'C';
        return 'F'
    }
}
